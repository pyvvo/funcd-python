"""Worker-side logic for the funcd Python pool host (ADR-0050), run INSIDE each subinterpreter by
``InterpreterPoolExecutor``. ``init`` loads the handler + the optional I/O validators once per worker
interpreter (state persists across invocations); ``invoke`` runs the contract + handler for one
request and returns a status-tagged envelope; ``load_error`` is a side-effect-free load probe.

No ``concurrent.*`` here — plain per-interpreter Python. ``init``/``invoke``/``ready`` are referenced
by the executor across the interpreter boundary, so they live in this small importable module (the
host puts the package dir on ``PYTHONPATH`` so the worker can import ``funcd_shim``)."""

from __future__ import annotations

import _locale
import inspect
import locale
import os
import subprocess
import sys
from collections.abc import Callable, MutableMapping
from typing import Any, AnyStr, NoReturn

from . import contract, jsonwire, runtime
from .blob import BlobClient
from .funclog import install_log_capture, open_channel
from .invoke import invoke as _invoke
from .kv import KVClient
from .runtime import Validators, call_handler
from .tracespan import InvocationSpan
from .types import CloudEvent, Handler

# Per-interpreter state, set by init() and read by invoke() — isolated to this worker interpreter.
_handler: Handler | None = None
_validators: Validators = Validators()
_channel: Any = None  # the shared telemetry channel (ADR-0101), opened once in init()
_member: str | None = None  # this worker's pool member name, sent on the local API and telemetry
_load_error: str | None = "not loaded"

#: The working directory, the umask, the C environment and the C locale belong to the process, not to a
#: subinterpreter: a member that changed them would change them for every sibling. The pool refuses them,
#: as a Node worker refuses process.chdir/process.umask and has no API to change the locale
#: (ADR-0044/0050 isolation parity).
_PROCESS_WIDE = ("chdir", "fchdir", "umask", "putenv", "unsetenv")


class _MemberEnviron(os._Environ[AnyStr]):
    """A member's os.environ is its own copy, as process.env is in a Node worker: a write skips
    putenv/unsetenv, so it never reaches the C environment that getenv reads and later members copy.
    The member's child processes (subprocess, os.system, os.posix_spawn) get this copy. time.tzset()
    still reads the process TZ: the local time zone belongs to the process, so a member cannot change it."""

    _data: MutableMapping[AnyStr, AnyStr]

    def __setitem__(self, key: AnyStr, value: AnyStr) -> None:
        self._data[self.encodekey(key)] = self.encodevalue(value)

    def __delitem__(self, key: AnyStr) -> None:
        try:
            del self._data[self.encodekey(key)]
        except KeyError:
            raise KeyError(key) from None


_POPEN_SIGNATURE = inspect.signature(subprocess.Popen)


class _MemberPopen(subprocess.Popen[Any]):
    """A child process gets the member's os.environ, as a Node worker's child_process gets the worker's
    process.env: without an env, subprocess would hand the child the process environment."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        bound = _POPEN_SIGNATURE.bind(*args, **kwargs)
        if bound.arguments.get("env") is None:
            bound.arguments["env"] = os.environ
        super().__init__(*bound.args, **bound.kwargs)


def _with_member_env(spawn: Callable[..., int]) -> Callable[..., int]:
    def spawn_with_member_env(path: Any, argv: Any, env: Any, /, **kwargs: Any) -> int:
        return spawn(path, argv, os.environ if env is None else env, **kwargs)

    return spawn_with_member_env


def _member_system(command: str | bytes) -> int:
    """os.system with the member's environment: C system() runs /bin/sh with the process environment."""
    pid = os.posix_spawn("/bin/sh", ["sh", "-c", command], os.environ)
    return os.waitpid(pid, 0)[1]


def _refuse(name: str) -> Callable[..., NoReturn]:
    def refused(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError(
            f"os.{name} is not supported in a pooled handler: every handler in the pool shares that state"
        )

    return refused


def _query_only(setlocale: Callable[[int, str | None], str]) -> Callable[[int, str | None], str]:
    # A set to the current locale changes nothing, so the stdlib's save-and-restore keeps working, and the
    # refusal is setlocale's own locale.Error, which callers already handle (getpreferredencoding does).
    def guarded(category: int, name: str | None = None, /) -> str:
        current = setlocale(category, None)
        if name is None or name == current:
            return current
        raise locale.Error(
            "locale.setlocale cannot change the locale in a pooled handler: every handler shares it"
        )

    return guarded


def _isolate_process_state() -> None:
    for module in (os, sys.modules[os.name]):
        for name in _PROCESS_WIDE:
            setattr(module, name, _refuse(name))
        for name in ("posix_spawn", "posix_spawnp"):
            setattr(module, name, _with_member_env(getattr(module, name)))
        module.system = _member_system  # type: ignore[attr-defined]
    subprocess.Popen = _MemberPopen  # type: ignore[misc]
    # locale.setlocale reaches the C call through locale._setlocale, its import-time copy.
    guarded = _query_only(_locale.setlocale)
    for module, name in ((_locale, "setlocale"), (locale, "_setlocale")):
        setattr(module, name, guarded)
    os.environ.__class__ = _MemberEnviron
    if os.supports_bytes_environ:
        os.environb.__class__ = _MemberEnviron


def init(
    src: str,
    artifact: str,
    handler: str,
    contract_path: str | None = None,
    write_lock: tuple[int, int] | None = None,
    name: str | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Load the handler + I/O validators into this interpreter (the materialization shape-gate,
    ADR-0058/0123). Runs once per worker. A load failure is kept as text for ``load_error``, not
    raised: a raising initializer breaks the executor and loses the error. *name* is the pool member,
    *env* its own environment, which no sibling sees.

    ADR-0123: when *contract_path* is given, compile the validators from the delivered schema
    (``fastjsonschema.compile``) **before** the untrusted handler module is imported — the bounded
    eval-free reversal + the m3 reorder. A set-but-broken path fails the worker closed. When absent,
    fall back to the module-baked ``__funcd_validate_*`` (transition back-compat)."""
    global _handler, _validators, _channel, _member, _load_error
    _isolate_process_state()
    # After the isolation: a member's environ writes skip putenv, so they stay in this interpreter.
    os.environ.update(env or {})
    _member = name
    if src not in sys.path:
        sys.path.insert(0, src)

    # Path B capture (ADR-0081) + traces (ADR-0101): each pool worker runs in its own subinterpreter
    # with its own root logger, so open the channel + install capture here (per-interpreter), before
    # the handler loads. One shared channel per worker. No-op unless FUNCD_LOG_FD/SOCK is set. Every
    # worker writes to the same FUNCD_LOG_FD, so they all take the host's one *write_lock*.
    _channel = open_channel(write_lock)
    install_log_capture(_channel, member=name)

    try:
        # ADR-0123: compile the delivered contract AHEAD of the handler import (m3 reorder).
        delivered = contract.load_from_path(contract_path) if contract_path else None
        module = runtime.load_module(artifact)
        _handler = runtime.resolve_handler(module, handler)
        _validators = delivered if delivered is not None else runtime.resolve_validators(module)
    except (Exception, SystemExit) as err:  # noqa: BLE001 - any load failure fails this member alone
        _load_error = f"{type(err).__name__}: {err}"
        return
    _load_error = None


def load_error() -> str | None:
    """A load probe: None once init() loaded the handler (no handler call), else why it did not."""
    return _load_error


class _Ctx:
    def log(self, *args: object) -> None:
        print(*args, flush=True)

    def invoke(self, alias: str, payload: Any) -> Any:
        return _invoke(alias, payload, member=_member)

    @property
    def kv(self) -> KVClient:
        return KVClient(_member)

    @property
    def blob(self) -> BlobClient:
        return BlobClient(_member)


def _reply(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"status": status, "body": jsonwire.encode(body)}


def invoke(
    body: bytes | str,
    traceparent: str | None = None,
    fn_name: str = "invoke",
    span_id: str | None = None,
    links: list[str] | None = None,
) -> dict[str, Any]:
    """Run one request: parse → optional input validation → handler → optional output validation →
    a status-tagged envelope the host maps to the HTTP response (identical to the solo shim). The body
    is encoded here, so a result with no JSON form is this handler's 500 and never reaches the host.
    ADR-0101: a successful-past-input-validation request emits a SERVER span on the worker's channel."""
    if _handler is None:  # defensive — init() always runs first
        return _reply(500, {"error": "handler not loaded"})
    try:
        event: CloudEvent[Any] = jsonwire.decode(body) if body else CloudEvent()
    except ValueError:
        return {"status": 400, "text": "invalid CloudEvent JSON"}
    if not isinstance(event, dict):
        # A valid-JSON but non-object body (null / array / scalar) is not a CloudEvent envelope.
        # Reject it cleanly — never let `event.get("data")` raise AttributeError and crash the pooled
        # worker (that surfaced as a gateway `proxy error: EOF` / empty-body 502).
        return {"status": 400, "text": "request body must be a JSON object (CloudEvent envelope)"}
    if _validators.input is not None:
        errors = _validators.input(event.get("data"))
        if errors:
            # ADR-0101: input-mismatch short-circuits before the handler → no invocation, no span.
            return _reply(422, {"error": "event data does not match the input contract", "details": errors})
    with InvocationSpan(_channel, fn_name, traceparent, span_id, links, _member) as span:
        try:
            result = call_handler(_handler, _Ctx(), event)
        except BaseException as err:  # noqa: BLE001 - user handler errors, SystemExit too, become 500
            span.fail(str(err))
            return _reply(500, {"error": str(err)})
        if _validators.output is not None:
            errors = _validators.output(result)
            if errors:
                span.fail("handler result does not match the output contract")
                return _reply(
                    500, {"error": "handler result does not match the output contract", "details": errors}
                )
        if result is None:
            return {"status": 204}
        try:
            return {"status": 200, "body": jsonwire.encode(result)}
        except Exception as err:  # noqa: BLE001 - a result with no JSON form is a handler failure
            span.fail(str(err))
            return _reply(500, {"error": str(err)})
