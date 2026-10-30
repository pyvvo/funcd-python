"""funcd Python runtime shim + the typed authoring contract.

Function authors import the contract types::

    from funcd_shim import Handler, CloudEvent, FunctionContext

    def handle(context: FunctionContext, event: CloudEvent) -> dict:
        ...

The shim entrypoint is ``python -m funcd_shim`` (see :mod:`funcd_shim.shim`). It serves the
runtime-shim HTTP contract; funcd checks each call against the Function's contract (funcd ADR-0303).
"""

from __future__ import annotations

from .types import CloudEvent, DeadlineExceeded, FunctionContext, Handler, Json

__all__ = ["CloudEvent", "DeadlineExceeded", "FunctionContext", "Handler", "Json"]
