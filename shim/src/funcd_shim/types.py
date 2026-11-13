"""The funcd Python function programming model (typed authoring contract).

A function module exports ``handle(context, event)``; the runtime shim invokes it once per
CloudEvent. Authors ``from funcd_shim import Handler, CloudEvent, FunctionContext`` for a typed
handler signature checked by ``mypy --strict`` — the same contract the shim enforces at runtime
(ADR-0049), mirroring ``@funcd/shim-nodejs``'s ``types.ts``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from .blob import BlobClient
    from .config import ConfigClient
    from .kv import KVClient
    from .lake import LakeClient
    from .secret import SecretClient
    from .sql import SQLClient

#: Json — the explicit "arbitrary JSON value" contract type (ADR-0058). Declare ``FuncInput``/
#: ``FuncOutput = Json``, or a field ``payload: Json``, when the shape is genuinely unknown; the
#: generated contract is the empty schema ``{}`` (accepts any JSON). Python has no ``unknown``, so
#: this aliases ``Any`` — but the named ``Json`` documents the intent (arbitrary JSON).
type Json = Any


class CloudEvent[T](TypedDict, total=False):
    """A CloudEvent — the normalized trigger envelope (ADR-0023). Generic in the payload type, so
    an author writes ``event: CloudEvent[FuncInput]`` and ``event["data"]`` is typed as ``FuncInput``
    (a TypedDict) — type-checked AND runtime-honest (the runtime value is a plain dict). All fields
    optional at the type level so a handler can read what it needs."""

    id: str
    source: str
    type: str
    specversion: str
    time: str
    datacontenttype: str
    subject: str
    data: T


@runtime_checkable
class FunctionContext(Protocol):
    """The per-invocation context the shim passes to the handler."""

    def log(self, *args: object) -> None:
        """Prints the arguments, separated by spaces, as one line on standard output, which funcd keeps as a
        log line at level INFO."""
        ...

    def invoke(self, alias: str, payload: Any) -> Any:
        """Calls the function that the link named ``alias`` targets, and returns its result parsed from
        JSON, or ``None`` when it returns nothing. The target's ``event["data"]`` is ``payload``, except that
        a dict with a top-level ``"data"`` or ``"specversion"`` key is taken as the whole CloudEvent, so
        ``event["data"]`` is only its ``"data"`` value: send such a dict inside an envelope, as
        ``{"specversion": "1.0", "data": payload}``. The link's timeout, 30 seconds by default, bounds the
        call. Fails with the HTTP status 403 when the function declares no link named ``alias``, 409 when the
        target serves HTTP, 422 when ``payload`` does not match the target's contract, and the target's
        status when its handler fails, such as 500."""
        ...

    @property
    def kv(self) -> KVClient:
        """The key-value tables that the function binds, by alias: read, write and list their keys."""
        ...

    @property
    def blob(self) -> BlobClient:
        """The Bucket prefixes that the function binds, by alias: read, write, delete and list their
        objects, whose keys are relative to the binding's prefix."""
        ...

    @property
    def sql(self) -> SQLClient:
        """The SQL databases that the function binds, by alias: ``query`` reads rows, ``query_values`` keeps
        every column of a join, and ``execute`` writes in one transaction. A call fails with the HTTP status
        503 while the database's engine is not Ready, or when a statement runs past funcd's SQL call timeout,
        30 seconds by default."""
        ...

    @property
    def config(self) -> ConfigClient:
        """The keys of the ConfigMaps that the function binds, with the values that the worker got when it
        started. A key that starts with ``FUNCD_`` is left out, since funcd reserves that prefix. A key that a
        bound Secret also holds is left out too, and ``ctx.secret`` reads the Secret's value. While funcd's S3
        gateway runs, a function that binds a Bucket reads the ``AWS_ACCESS_KEY_ID`` and
        ``AWS_SECRET_ACCESS_KEY`` that funcd issues it, in place of a ConfigMap's."""
        ...

    @property
    def secret(self) -> SecretClient:
        """The Secrets that the function binds. ``get`` and ``list`` read their keys and values at each call,
        with no cache, so a changed Secret shows at the next read; a key that starts with ``FUNCD_`` is never
        read: ``get`` returns ``None`` for it, and ``list`` leaves it out. ``put`` and ``delete`` change one
        key of a bound Secret that the function may write, as the Secret's owner or through a writer role such
        as Secret Data Writer."""
        ...

    @property
    def lake(self) -> LakeClient:
        """The Lakes that the function binds, by alias: the address of each Lake's gateway, and the token
        that the gateway accepts from the function."""
        ...

    def remaining_ms(self) -> int | None:
        """The whole milliseconds left before the call's deadline, 0 once it has passed, or ``None`` when
        the call has none."""
        ...


class DeadlineExceeded(BaseException):
    """Raised in a handler's thread after its deadline (ADR-0226). Catch it only to clean up."""

    def __init__(self, *args: object) -> None:
        # PyThreadState_SetAsyncExc raises the class without arguments, and a raise answers with its message.
        super().__init__(*(args or ("deadline exceeded",)))


class Handler(Protocol):
    """A function handler: receives the context + CloudEvent, returns a response (or ``None``).

    A returned ``dict``/``list`` becomes the HTTP 200 JSON body; ``None`` → 204; a raised
    exception → 500.
    """

    def __call__(self, context: FunctionContext, event: CloudEvent[Any]) -> Any: ...
