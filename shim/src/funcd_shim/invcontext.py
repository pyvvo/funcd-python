"""Per-invocation context carrier for the Python runtime shim (ADR-0101).

A ``contextvars.ContextVar`` the shim sets around each handler call so BOTH the trace span
(``tracespan``) and the log capture (``funclog._format_ndjson`` reads it) tag their records with the
SAME inv/trace/span ids — the logs↔trace correlation that closes ADR-0081's provenance open question
(the Python shim's log records carried empty inv/trace/span until now). Stdlib only.

Per-thread: ``ThreadingHTTPServer`` runs each request on its own thread, and a ``ContextVar`` set in
that thread is read back by the logging handler on the same thread — so the correlation is exact.
It also carries the call's deadline, which ``context.remaining_ms()`` reads (funcd ADR-0226).
"""

from __future__ import annotations

import contextvars
import math
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class InvContext:
    """The identity of the currently-executing invocation."""

    inv: str  # per-invocation id (hex16)
    trace_id: str  # hex32 — adopted from traceparent or minted (root)
    span_id: str  # hex16 — this invocation's span
    parent_id: str  # hex16 — the traceparent span-id, "" for a root
    deadline: float | None = None  # time.monotonic() from X-Funcd-Timeout-Ms, None without the header


_current: contextvars.ContextVar[InvContext | None] = contextvars.ContextVar("funcd_inv", default=None)


def current_inv() -> InvContext | None:
    """The active invocation context, or ``None`` outside an invocation (⇒ log records emit empty
    inv/trace/span, exactly as before — back-compat with the ADR-0081 wire)."""
    return _current.get()


def remaining_ms() -> int | None:
    """Milliseconds left before the current call's deadline, floored at 0; None outside a call or
    without a deadline."""
    inv = _current.get()
    if inv is None or inv.deadline is None:
        return None
    return max(0, math.floor((inv.deadline - time.monotonic()) * 1000))


def set_inv(ctx: InvContext) -> contextvars.Token[InvContext | None]:
    """Bind ``ctx`` for the current thread's context; pass the token to :func:`reset_inv`."""
    return _current.set(ctx)


def reset_inv(token: contextvars.Token[InvContext | None]) -> None:
    """Clear the invocation context bound by :func:`set_inv`."""
    _current.reset(token)
