"""Trace capture producer for the Python runtime shim (ADR-0101).

The shim mints one per-invocation OTel SERVER span: it adopts an incoming W3C ``traceparent`` (else
mints a root trace), runs the handler inside the invocation context, and emits ONE span record on the
SAME funclog side channel as logs — tagged ``"funcd.signal": "traces"`` so the host demux
(``funclog.Route``) routes it to the trace sink. The auto span is zero-touch for the function. Stdlib
only, so it ships with the shim.
"""

from __future__ import annotations

import contextvars
import json
import re
import secrets
import time
from typing import TYPE_CHECKING, Any

from .funclog import _Budget, _record_bound
from .invcontext import InvContext, current_inv, reset_inv, set_inv

if TYPE_CHECKING:
    from .funclog import Channel

_ZERO_TRACE = "0" * 32
_ZERO_SPAN = "0" * 16
_TRACE_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_RE = re.compile(r"^[0-9a-f]{16}$")
_VERSION_RE = re.compile(r"^[0-9a-f]{2}$")


def parse_traceparent(tp: str | None) -> tuple[str, str] | None:
    """Parse a W3C ``traceparent`` (``00-<trace32>-<span16>-<flags>``), returning
    ``(trace_id, parent_id)`` or ``None`` when absent/malformed/all-zero (⇒ the caller mints a root)."""
    if not tp:
        return None
    parts = tp.strip().split("-")
    if len(parts) < 4:
        return None
    version, trace_id, parent_id = parts[0], parts[1], parts[2]
    if not _VERSION_RE.match(version) or version == "ff":
        return None
    if not _TRACE_RE.match(trace_id) or trace_id == _ZERO_TRACE:
        return None
    if not _SPAN_RE.match(parent_id) or parent_id == _ZERO_SPAN:
        return None
    return trace_id, parent_id


def new_inv_context(tp: str | None, provided_span_id: str | None = None) -> InvContext:
    """Establish the invocation identity: adopt the traceparent's trace-id + parent span-id when
    present, else mint a root (fresh 16-byte trace). The span-id is the engine-provided one (ADR-0105,
    X-Funcd-Span-Id) when a valid hex16 is given, else freshly minted (a direct invoke)."""
    adopted = parse_traceparent(tp)
    if provided_span_id and _SPAN_RE.match(provided_span_id):
        span_id = provided_span_id  # ADR-0105: use the engine-provided id
    else:
        span_id = secrets.token_hex(8)
    return InvContext(
        inv=secrets.token_hex(8),
        trace_id=adopted[0] if adopted else secrets.token_hex(16),
        span_id=span_id,
        parent_id=adopted[1] if adopted else "",
    )


def parse_links(header: str | None) -> list[str]:
    """Split an ``X-Funcd-Span-Links`` header (comma-separated hex16 span-ids) into a validated list."""
    if not header:
        return []
    return [s.strip() for s in header.split(",") if s.strip() and _SPAN_RE.match(s.strip())]


def _emit_span(
    channel: Channel,
    ids: InvContext,
    name: str,
    kind: str,
    start_ns: int,
    end_ns: int,
    status: str,
    status_msg: str,
    attrs: dict[str, str],
    links: list[str],
    member: str | None,
    bound: int,
) -> None:
    """Write one span record. *ids* names the span (trace, span, parent, inv): the invocation's own
    context for a SERVER span, the caller's trace and invocation with a fresh span-id under the caller's
    span for a CLIENT span (ADR-0165). The line is at most *bound* bytes (ADR-0168): an over-long
    ``status_msg`` is cut and the record carries ``attrs.truncated`` and ``attrs.keptBytes`` (the kept
    ``status_msg`` bytes)."""
    rec: dict[str, Any] = {
        "funcd.signal": "traces",
        "trace_id": ids.trace_id,
        "span_id": ids.span_id,
        "parent_id": ids.parent_id,
        "name": name,
        "kind": kind,
        "start": start_ns,
        "end": end_ns,
        "status": status,
        "status_msg": status_msg,
        "attrs": attrs,
        "inv": ids.inv,
        "links": links,  # ADR-0105: fan-in edges (same-trace span-ids)
    }
    if member:
        rec["funcd.member"] = member
    text = json.dumps(rec, separators=(",", ":"))
    if len(text) > bound:
        rec = {**rec, "status_msg": "", "attrs": {**attrs, "truncated": "true", "keptBytes": ""}}
        # keptBytes is at most 7 digits: the bound is at most 1 MiB.
        budget = _Budget(bound - len(json.dumps(rec, separators=(",", ":"))) - 7)
        rec["status_msg"] = budget.take(status_msg)
        rec["attrs"]["keptBytes"] = str(budget.kept)
        text = json.dumps(rec, separators=(",", ":"))
    channel.write_line((text + "\n").encode("utf-8"))  # best-effort; _Channel swallows OSError


class InvocationSpan:
    """A live per-invocation SERVER span. Enter to bind the context (so logs correlate); exit emits
    the span record with the outcome. ``status``/``status_msg`` default to OK unless :meth:`fail` set."""

    def __init__(
        self,
        channel: Channel | None,
        name: str,
        tp: str | None,
        span_id: str | None = None,
        links: list[str] | None = None,
        member: str | None = None,
        max_record_bytes: int | None = None,
    ) -> None:
        self._channel = channel
        self._bound = _record_bound() if max_record_bytes is None else max_record_bytes
        self._member = member
        self._name = name
        self._ctx = new_inv_context(tp, span_id)
        self._links = links or []
        self._status = "OK"
        self._status_msg = ""
        self._start_ns = 0
        self._t0 = 0
        self._token: contextvars.Token[InvContext | None] | None = None

    @property
    def ctx(self) -> InvContext:
        return self._ctx

    def fail(self, message: str) -> None:
        self._status = "ERROR"
        self._status_msg = message

    def __enter__(self) -> InvocationSpan:
        self._start_ns = time.time_ns()
        self._t0 = time.monotonic_ns()
        self._token = set_inv(self._ctx)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc is not None and self._status != "ERROR":
            self.fail(str(exc))
        if self._token is not None:
            reset_inv(self._token)
        if self._channel is None:
            return
        end_ns = self._start_ns + (time.monotonic_ns() - self._t0)
        _emit_span(
            self._channel,
            self._ctx,
            self._name,
            "SERVER",
            self._start_ns,
            end_ns,
            self._status,
            self._status_msg,
            {},
            self._links,
            self._member,
            self._bound,
        )


class ClientSpan:
    """CLIENT span "call <alias>" of one context.invoke call, under current_inv() (ADR-0165);
    outside an invocation traceparent is None, nothing emitted. With no channel the traceparent is still
    minted and no span line is written. *member* is the calling pool member, stamped as ``funcd.member``;
    the span line takes the record bound as the SERVER span's does (ADR-0168)."""

    def __init__(
        self,
        channel: Channel | None,
        alias: str,
        member: str | None = None,
        max_record_bytes: int | None = None,
    ) -> None:
        self._channel = channel
        self._bound = _record_bound() if max_record_bytes is None else max_record_bytes
        self._alias = alias
        self._member = member
        caller = current_inv()
        self._ids = (
            None
            if caller is None
            else InvContext(
                inv=caller.inv,
                trace_id=caller.trace_id,
                span_id=secrets.token_hex(8),
                parent_id=caller.span_id,
            )
        )
        self._http_status: int | None = None
        self._start_ns = 0
        self._t0 = 0

    @property
    def traceparent(self) -> str | None:
        if self._ids is None:
            return None
        return f"00-{self._ids.trace_id}-{self._ids.span_id}-01"

    def reply(self, status: int) -> None:
        """Record the local API reply status as http.status_code, a decimal string;
        sets no status (invoke.py raises on non-2xx)."""
        self._http_status = status

    def __enter__(self) -> ClientSpan:
        self._start_ns = time.time_ns()
        self._t0 = time.monotonic_ns()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Emit the span once; any exception sets ERROR with str(exc)."""
        if self._ids is None or self._channel is None:
            return
        end_ns = self._start_ns + (time.monotonic_ns() - self._t0)
        status, status_msg = ("OK", "") if exc is None else ("ERROR", str(exc))
        attrs = {} if self._http_status is None else {"http.status_code": str(self._http_status)}
        _emit_span(
            self._channel,
            self._ids,
            f"call {self._alias}",
            "CLIENT",
            self._start_ns,
            end_ns,
            status,
            status_msg,
            attrs,
            [],
            self._member,
            self._bound,
        )
