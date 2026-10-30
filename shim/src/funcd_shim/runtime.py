"""Handler resolution for the funcd Python shim (the materialization shape-gate).

No HTTP here — just loading the artifact module and resolving the handler, mirroring the Node shim's
``runtime.ts`` (ADR-0030 §3). A shape failure raises :class:`ShapeError`; the shim turns that into
exit code 3.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from types import ModuleType
from typing import Any, cast

from .deadline import Call
from .invcontext import current_inv
from .types import CloudEvent, DeadlineExceeded, FunctionContext, Handler


class ShapeError(Exception):
    """The artifact does not satisfy the runtime shape-gate (ADR-0030 §3): a missing/non-callable
    handler. The shim exits 3."""


def load_module(artifact: str) -> ModuleType:
    """Import the artifact ``.py`` file as a module. Raises :class:`ShapeError` if it cannot be
    loaded (syntax error, missing file) — a shape-gate failure."""
    spec = importlib.util.spec_from_file_location("funcd_function", artifact)
    if spec is None or spec.loader is None:
        raise ShapeError(f"cannot load artifact {artifact!r}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as err:  # noqa: BLE001 - any import-time failure is a shape error
        raise ShapeError(f"artifact {artifact!r} failed to import: {err}") from err
    return module


def resolve_handler(module: ModuleType, name: str) -> Handler:
    """Pick the handler export ``name`` (default ``handle``). A missing or non-callable export
    is a shape-gate failure (ADR-0030 §3)."""
    candidate = getattr(module, name, None)
    if not callable(candidate):
        raise ShapeError(f'export "{name}" is not callable')
    return cast(Handler, candidate)


def call_handler(handler: Handler, context: FunctionContext, event: CloudEvent[Any]) -> Any:
    """Call *handler* and return its result, running a coroutine (an ``async def`` handler) to
    completion first: the Node shim awaits its handler, and ADR-0049 keeps the same contract. A call
    whose context carries a deadline runs under its watchdog, and not at all once its stop has passed
    (funcd ADR-0226)."""
    inv = current_inv()
    if inv is None or inv.deadline is None:
        return _run(handler, context, event, None)
    call = Call(inv.deadline)
    try:
        call.start()
        return _run(handler, context, event, call)
    finally:
        try:
            call.end()
        except DeadlineExceeded:
            # It fired on entering end(), before end()'s own try. It was the call's only raise, so
            # end() now runs through.
            call.end()


def _run(handler: Handler, context: FunctionContext, event: CloudEvent[Any], call: Call | None) -> Any:
    result = handler(context, event)
    if not inspect.iscoroutine(result):
        return result
    with asyncio.Runner() as runner:
        return runner.run(result if call is None else call.track(result))


__all__ = [
    "ShapeError",
    "call_handler",
    "load_module",
    "resolve_handler",
]
