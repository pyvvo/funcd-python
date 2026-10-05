"""Path B function-log capture producer for the Python runtime shim (ADR-0081).

Python has no ``console`` (the Node Path B hook point), so the structured capture seam is a
``logging.Handler`` installed on the **root logger**: every ``logging.info(...)`` / ``logging.error(...)``
a function emits becomes one NDJSON record on a dedicated side channel that funcd reads host-side. A bare
``print(...)`` is intentionally **not** captured here — it falls to Path A (raw stdout, coarse ``INFO``).

The channel is selected once at startup from the environment (set by the funcd launch path, ADR-0011):

  - ``FUNCD_LOG_FD``   — a numeric fd (e.g. ``"3"``) funcd passed in (crun ``--preserve-fds``);
                         lines are written with a synchronous ``os.write`` (the crash-tail trade, ADR-0081).
  - ``FUNCD_LOG_SOCK`` — a Unix-domain socket path (containerd bind-mount); a connected ``AF_UNIX`` stream.
  - neither set       — no capture: ``install_log_capture()`` is a no-op and ``logging`` behaves normally.

The host ``Reader`` is language-agnostic: this emits the **same NDJSON wire** as the Node shim, only with
``"funcd.source": "logging"`` (vs Node's ``"console"``). One JSON object + ``"\n"`` per record. Stdlib only.
"""

from __future__ import annotations

import codecs
import json
import logging
import os
import re
import socket
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager
from typing import Protocol

from .invcontext import current_inv


class Channel(Protocol):
    """The write side of the telemetry side channel — the seam log + trace capture share (ADR-0101).
    ``_Channel`` (fd/UDS) is the runtime implementation; tests supply their own."""

    def write_line(self, line: bytes) -> None: ...


# ADR-0081 wire: levelno -> severity token. WARNING->WARN, CRITICAL->FATAL; anything unknown -> INFO.
_SEV_BY_LEVELNO: dict[int, str] = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARN",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "FATAL",
}

# LogRecord attributes that are intrinsic to the record (not user-supplied via extra=). Anything on the
# record's __dict__ NOT in this set is an `extra` field the function attached and is forwarded into attrs.
_INTRINSIC_RECORD_KEYS = frozenset(
    logging.makeLogRecord({}).__dict__.keys() | {"message", "asctime", "taskName"}
)


class _PipeLock:
    """A mutex over a pipe that holds one token byte. Pool workers are subinterpreters that share no
    Python object, only the process's fds, so this is a lock they can all take."""

    def __init__(self, fds: tuple[int, int]) -> None:
        self._read_fd, self._write_fd = fds

    def __enter__(self) -> None:
        os.read(self._read_fd, 1)

    def __exit__(self, *exc: object) -> None:
        os.write(self._write_fd, b"\0")


def new_write_lock() -> tuple[int, int]:
    """Create the write lock that every writer of one shared fd channel takes, as the fds of a
    ``_PipeLock``: ints cross into each pool worker's interpreter."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"\0")
    return read_fd, write_fd


class _Channel:
    """The side channel — an fd (``os.write``) or a connected ``AF_UNIX`` socket (``sendall``)."""

    def __init__(
        self,
        *,
        fd: int | None = None,
        sock: socket.socket | None = None,
        lock: AbstractContextManager[None] | None = None,
    ) -> None:
        self._fd = fd
        self._sock = sock
        # The solo shim's request threads share one channel, and spans are written outside the logging
        # lock. Neither a pipe write longer than PIPE_BUF nor a sendall is atomic: every write holds this.
        self._lock = lock or threading.Lock()

    def write_line(self, line: bytes) -> None:
        # Synchronous, best-effort: a broken channel must never crash the user's handler.
        try:
            with self._lock:
                if self._sock is not None:
                    self._sock.sendall(line)
                elif self._fd is not None:
                    # os.write may write only part of the line (a signal mid-write); finish it, like sendall.
                    rest = memoryview(line)
                    while rest:
                        rest = rest[os.write(self._fd, rest) :]
        except OSError:
            pass


def open_channel(write_lock: tuple[int, int] | None = None) -> _Channel | None:
    """Resolve the telemetry channel from the env contract, or ``None`` when neither var is set.
    Public so an entrypoint opens the channel ONCE and shares it between log + trace capture
    (ADR-0101: both signals ride the one channel; a second connect would double-capture).
    *write_lock* (from ``new_write_lock``) serializes the fd writes of every interpreter that shares it."""
    return _open_channel(write_lock)


def _open_channel(write_lock: tuple[int, int] | None = None) -> _Channel | None:
    """Resolve the channel from the env contract, or ``None`` when neither var is set."""
    fd_env = os.environ.get("FUNCD_LOG_FD")
    if fd_env:
        try:
            return _Channel(fd=int(fd_env), lock=_PipeLock(write_lock) if write_lock else None)
        except (ValueError, OSError):
            return None
    sock_path = os.environ.get("FUNCD_LOG_SOCK")
    if sock_path:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(sock_path)
            return _Channel(sock=sock)
        except OSError:
            return None
    return None


# ADR-0168: one record line is at most FUNCD_FUNCLOG_MAX_RECORD_BYTES bytes before its "\n"; funcd's
# reader drops a line over 1 MiB.
_DEFAULT_MAX_RECORD_BYTES = 65536

# What the cut marker adds: ,"truncated":"true","keptBytes":"<at most 7 digits>" (the bound is ≤ 1 MiB).
_MARKER_RESERVE = 41

# extra= keys that would clobber the lossless capture or the cut marker ("args" is intrinsic already).
_RESERVED_KEYS = frozenset({"truncated", "keptBytes"})

# %-conversions of a log message: an optional (key), flags, width, precision, length and the type.
_SPEC = re.compile(
    r"%(?:\((?P<key>[^)]*)\))?(?P<fmt>[#0\- +]*(?:\d+)?(?:\.\d+)?)[hlL]?(?P<conv>[diouxXeEfFgGcrsa%])"
)


def _record_bound() -> int:
    """``FUNCD_FUNCLOG_MAX_RECORD_BYTES``; unset or not a positive integer gives the default."""
    raw = os.environ.get("FUNCD_FUNCLOG_MAX_RECORD_BYTES", "")
    if raw.isascii() and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_MAX_RECORD_BYTES


def _escaped_len(text: str) -> int:
    """The bytes *text* takes as a JSON string value of the record line (``json.dumps`` escapes non-ASCII)."""
    return len(json.dumps(text)) - 2


class _Budget:
    """A record's byte budget: ``left`` is what the line may still grow by, ``kept`` the UTF-8 bytes of the
    values kept so far (before JSON escaping), ``cut`` is set once a value did not fit."""

    __slots__ = ("cut", "kept", "left")

    def __init__(self, left: int) -> None:
        self.left = max(0, left)
        self.kept = 0
        self.cut = False

    def take(self, text: str) -> str:
        """The longest prefix of *text* whose escaped bytes fit; marks the budget cut when *text* did not
        fit whole. It looks at no more than ``left`` + 1 characters."""
        if self.cut:
            return ""
        head = text[: self.left + 1]
        if len(head) == len(text) and _escaped_len(text) <= self.left:
            kept = text
        else:
            lo, hi = 0, len(head)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if _escaped_len(head[:mid]) <= self.left:
                    lo = mid
                else:
                    hi = mid - 1
            kept = head[:lo]
            self.cut = True
        self.left -= _escaped_len(kept)
        self.kept += len(kept.encode("utf-8", "surrogatepass"))
        return kept

    def charge(self, n: int) -> bool:
        """Take *n* bytes of structure (a key, quotes, a comma), or mark the budget cut."""
        if self.cut or n > self.left:
            self.cut = True
            return False
        self.left -= n
        return True

    def copy(self) -> _Budget:
        scratch = _Budget(self.left)
        scratch.cut = self.cut
        return scratch


_Put = Callable[[str], object]

# The built-in types whose str() is their repr() and whose repr() the walker writes itself.
_WALKED = (list, tuple, dict, set, frozenset, bytes, bytearray)


def _walked_type(value: object) -> type | None:
    """The built-in type the walker writes *value* as, or ``None`` when its repr or str is its own."""
    for base in _WALKED:
        if isinstance(value, base):
            t = type(value)
            if t.__repr__ is base.__repr__ and t.__str__ is base.__str__:
                return base
            return None
    return None


def _bytes_repr(value: bytes | bytearray, put: _Put, budget: _Budget) -> None:
    """repr() of *value*, escaping no more bytes than the budget can hold."""
    quote = '"' if b"'" in value and b'"' not in value else "'"
    head = bytes(value[: budget.left + 1])
    body = codecs.escape_encode(head)[0].decode("ascii")
    if quote == '"':
        body = body.replace("\\'", "'")
    prefix = "bytearray(b" if isinstance(value, bytearray) else "b"
    put(prefix + quote + body)
    if len(head) == len(value):
        put(quote + (")" if isinstance(value, bytearray) else ""))


def _str_repr(value: str, put: _Put, budget: _Budget) -> None:
    """repr() of a str, built from no more characters than the budget can hold."""
    head = value[: budget.left + 1]
    text = repr(head)
    if "'" in value and '"' in value and text[0] == '"':
        text = "'" + text[1:-1].replace("'", "\\'") + "'"
    put(text if len(head) == len(value) else text[:-1])


def _repr_walk(value: object, put: _Put, budget: _Budget, active: set[int]) -> None:
    if budget.cut:
        return
    if type(value) is str:
        _str_repr(value, put, budget)
        return
    kind = _walked_type(value)
    if kind is None:
        put(repr(value))
        return
    if kind is bytes or kind is bytearray:
        _bytes_repr(value, put, budget)  # type: ignore[arg-type]
        return
    if id(value) in active:
        put("{...}" if kind is dict else "[...]")
        return
    active.add(id(value))
    try:
        if kind is dict:
            put("{")
            for i, (k, v) in enumerate(value.items()):  # type: ignore[attr-defined]
                if budget.cut:
                    break
                if i:
                    put(", ")
                _repr_walk(k, put, budget, active)
                put(": ")
                _repr_walk(v, put, budget, active)
            put("}")
            return
        items: Iterable[object] = value  # type: ignore[assignment]
        if kind in (set, frozenset) and not value:
            put("set()" if kind is set else "frozenset()")
            return
        brackets: dict[type, tuple[str, str]] = {
            list: ("[", "]"),
            tuple: ("(", ")"),
            set: ("{", "}"),
            frozenset: ("frozenset({", "})"),
        }
        open_, close = brackets[kind]
        put(open_)
        n = 0
        for item in items:
            if budget.cut:
                break
            if n:
                put(", ")
            _repr_walk(item, put, budget, active)
            n += 1
        put("," + close if kind is tuple and n == 1 else close)
    finally:
        active.discard(id(value))


def _bounded_repr(value: object, budget: _Budget) -> str:
    """repr(*value*) until the budget runs out: containers, str, bytes, bytearray and scalars are walked;
    an object with its own repr runs it once and its text is cut."""
    parts: list[str] = []
    _repr_walk(value, lambda s: parts.append(budget.take(s)), budget, set())
    return "".join(parts)


def _bounded_str(value: object, budget: _Budget) -> str:
    """str(*value*) until the budget runs out: containers, bytes and bytearray are walked as repr()."""
    if type(value) is str:
        return budget.take(value)
    if _walked_type(value) is not None:
        return _bounded_repr(value, budget)
    return budget.take(str(value))


def _json_key(key: object) -> str:
    if isinstance(key, str):
        return key
    if key is True or key is False or key is None:
        return json.dumps(key)
    if isinstance(key, int):
        return int.__repr__(key)
    if isinstance(key, float):
        return json.dumps(key)
    raise TypeError(f"keys must be str, int, float, bool or None, not {type(key).__name__}")


def _json_walk(value: object, put: _Put, budget: _Budget, active: set[int]) -> None:
    if budget.cut:
        return
    if isinstance(value, str):
        put(json.dumps(value[: budget.left + 1], ensure_ascii=False))
    elif value is None or value is True or value is False:
        put(json.dumps(value))
    elif isinstance(value, int):
        put(int.__repr__(value))
    elif isinstance(value, float):
        put(json.dumps(value))
    elif isinstance(value, (list, tuple, dict)):
        if id(value) in active:
            raise ValueError("Circular reference detected")
        active.add(id(value))
        if isinstance(value, dict):
            put("{")
            for i, (k, v) in enumerate(value.items()):
                if budget.cut:
                    break
                put(
                    ("," if i else "") + json.dumps(_json_key(k)[: budget.left + 1], ensure_ascii=False) + ":"
                )
                _json_walk(v, put, budget, active)
            put("}")
        else:
            put("[")
            for i, item in enumerate(value):
                if budget.cut:
                    break
                if i:
                    put(",")
                _json_walk(item, put, budget, active)
            put("]")
        active.discard(id(value))
    else:
        # json.dumps(default=repr): the value's repr as a JSON string.
        put('"')
        _repr_walk(value, lambda s: put(json.dumps(s, ensure_ascii=False)[1:-1]), budget, set())
        put('"')


def _bounded_json(value: object, budget: _Budget) -> str:
    """*value* as compact JSON (``json.dumps(default=repr, ensure_ascii=False)``'s text) until the budget
    runs out; a cycle or a key JSON cannot hold falls back to ``_bounded_repr``, as ``repr`` did."""
    left, kept = budget.left, budget.kept
    parts: list[str] = []
    try:
        _json_walk(value, lambda s: parts.append(budget.take(s)), budget, set())
    except (TypeError, ValueError, RecursionError):
        budget.left, budget.kept, budget.cut = left, kept, False
        return _bounded_repr(value, budget)
    return "".join(parts)


def _render(spec: re.Match[str], arg: object, budget: _Budget) -> str:
    conv, fmt = spec["conv"], spec["fmt"]
    if conv == "s":
        return ("%" + fmt + "s") % _bounded_str(arg, budget)
    if conv in "ra":
        text = _bounded_repr(arg, budget)
        if conv == "a":
            text = text.encode("ascii", "backslashreplace").decode("ascii")
        return ("%" + fmt + "s") % text
    return ("%" + fmt + conv) % (arg,)


def _bounded_message(record: logging.LogRecord, budget: _Budget) -> str:
    """``record.getMessage()``'s text until the budget runs out: a non-str ``msg`` and each ``%s`` argument
    through ``_bounded_str``, ``%r``/``%a`` through ``_bounded_repr``, any other conversion as ``%``
    renders it; one mapping serves ``%(key)s``."""
    msg = record.msg if isinstance(record.msg, str) else _bounded_str(record.msg, budget.copy())
    if not record.args:
        return budget.take(msg)
    args = record.args
    try:
        specs = list(_SPEC.finditer(msg))
        keyed = any(m["key"] is not None for m in specs)
        positional: tuple[object, ...] = (
            () if keyed else (args,) if isinstance(args, Mapping) else tuple(args)
        )
        parts: list[str] = []
        at = used = 0
        for m in specs:
            parts.append(budget.take(msg[at : m.start()].replace("%%", "%")))
            at = m.end()
            if m["conv"] == "%":
                parts.append(budget.take("%"))
                continue
            if keyed:
                if not isinstance(args, Mapping):
                    raise TypeError("format requires a mapping")
                arg = args[m["key"]]
            else:
                arg = positional[used]
                used += 1
            parts.append(budget.take(_render(m, arg, budget.copy())))
        if not keyed and used != len(positional):
            raise TypeError("not all arguments converted during string formatting")
        parts.append(budget.take(msg[at:]))
        return "".join(parts)
    except (KeyError, IndexError, TypeError, ValueError):
        # A message % cannot format raises here as it does in logging, and the handler reports it.
        return budget.take(record.getMessage())


_FORMATTER = logging.Formatter()


class FuncLogHandler(logging.Handler):
    """A root-logger handler that emits each record as one NDJSON line on the side channel ONLY.

    It does not echo to stdout/stderr, so Path A (raw fd 1/2) never re-captures a Path B line.
    """

    def __init__(
        self,
        channel: Channel,
        member: str | None = None,
        max_record_bytes: int = _DEFAULT_MAX_RECORD_BYTES,
    ) -> None:
        super().__init__(level=logging.NOTSET)
        self._channel = channel
        self._member = member
        self._bound = max_record_bytes

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self._format_ndjson(record)
        except Exception:  # noqa: BLE001 - a formatting bug must not crash the handler
            self.handleError(record)
            return
        self._channel.write_line(line)

    def _format_ndjson(self, record: logging.LogRecord) -> bytes:
        """One record line of at most the bound (ADR-0168): the envelope (with ``lineno``) and the marker
        reserve first, then ``body``, ``logger``, ``funcName``, the exception, the stack and the extras,
        and ``args`` last; what does not fit is cut and the record carries ``truncated`` and ``keptBytes``."""
        # ADR-0101: tag with the active invocation context (set by the trace span around the handler)
        # so logs correlate with their span. Outside an invocation the context is None → empty ids.
        inv = current_inv()
        attrs: dict[str, str] = {"lineno": str(record.lineno)}
        obj: dict[str, object] = {
            "ts": time.time_ns(),
            "sev": _SEV_BY_LEVELNO.get(record.levelno, "INFO"),
            "body": "",
            "attrs": attrs,
            "inv": inv.inv if inv else "",
            "trace_id": inv.trace_id if inv else "",
            "span_id": inv.span_id if inv else "",
            "funcd.source": "logging",
        }
        if self._member:
            obj["funcd.member"] = self._member
        budget = _Budget(self._bound - len(json.dumps(obj, separators=(",", ":"))) - _MARKER_RESERVE)
        obj["body"] = _bounded_message(record, budget)

        fills: list[tuple[str, Callable[[], str]]] = [
            ("logger", lambda: _bounded_str(record.name, budget)),
            ("funcName", lambda: _bounded_str(record.funcName, budget)),
        ]
        # OpenTelemetry semantic-convention names: the attrs become OTLP LogRecord attributes host-side.
        if record.exc_info and record.exc_info[1] is not None:
            exc_info = record.exc_info
            exc = exc_info[1]
            fills.append(("exception.type", lambda: budget.take(type(exc).__name__)))
            fills.append(("exception.message", lambda: _bounded_str(exc, budget)))
            fills.append(("exception.stacktrace", lambda: budget.take(_FORMATTER.formatException(exc_info))))
        if record.stack_info:
            stack = record.stack_info
            fills.append(("code.stacktrace", lambda: budget.take(stack)))
        # Any extra={...} fields the function attached land on the record __dict__; forward them stringified.
        for key, value in record.__dict__.items():
            if key not in _INTRINSIC_RECORD_KEYS and key not in _RESERVED_KEYS:
                fills.append((key, lambda value=value: _bounded_str(value, budget)))  # type: ignore[misc]
        if record.args:
            fills.append(("args", lambda: _bounded_json(record.args, budget)))

        filled: dict[str, str] = {}
        for key, fill in fills:
            if not budget.charge(len(json.dumps(key)) + 4):
                break
            filled[key] = fill()
            if budget.cut:
                break
        # Today's key order: logger, funcName, lineno, args, then the rest as filled.
        ordered: dict[str, str] = {k: filled[k] for k in ("logger", "funcName") if k in filled}
        ordered["lineno"] = attrs["lineno"]
        if "args" in filled:
            ordered["args"] = filled["args"]
        ordered.update((k, v) for k, v in filled.items() if k not in ordered)
        if budget.cut:
            ordered["truncated"] = "true"
            ordered["keptBytes"] = str(budget.kept)
        obj["attrs"] = ordered
        return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def install_log_capture(
    channel: Channel | None = None, *, member: str | None = None
) -> FuncLogHandler | None:
    """Install Path B capture on the root logger if a channel is available; else do nothing.

    Returns the installed handler (or ``None`` when no channel is configured), mainly for tests.
    Call this EARLY in shim startup, before any function handler runs, so the first ``logging.info``
    a function emits is already captured. Pass ``channel`` to reuse a channel an entrypoint already
    opened (ADR-0101: log + trace capture share ONE channel); omit it to open from the env. A pool
    worker passes its *member* name, stamped on every record as ``funcd.member``.
    """
    if channel is None:
        channel = _open_channel()
    if channel is None:
        return None
    handler = FuncLogHandler(channel, member, _record_bound())
    root = logging.getLogger()
    # Capture INFO and above by default (so a function's logging.info(...) is seen). Lower the root
    # level only if it is currently coarser than INFO; never raise a more-verbose configuration.
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler
