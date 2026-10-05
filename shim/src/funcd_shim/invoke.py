"""Synchronous fn-to-fn invoke over the worker-node local API (HTTP-over-UDS, ADR-0064).

The platform bind-mounts the per-sandbox socket and provides its path via ``FUNCD_INVOKE_SOCKET``;
the handler calls ``context.invoke(alias, input)`` and the platform brokers the call to the linked
target. Stdlib-only (no third-party dependency).
"""

from __future__ import annotations

import http.client
import json
import os
import socket
from typing import TYPE_CHECKING, Any

from .tracespan import ClientSpan

if TYPE_CHECKING:
    from .funclog import Channel


class _UnixHTTPConnection(http.client.HTTPConnection):
    """An HTTPConnection that dials a Unix domain socket instead of TCP."""

    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self._socket_path)
        self.sock = sock


MEMBER_HEADER = "X-Funcd-Member"
"""The local API request header naming the calling pool member; the solo shim sends none."""


def invoke(alias: str, payload: Any, *, member: str | None = None, channel: Channel | None = None) -> Any:
    """POST *payload* to ``/invoke/<alias>`` over the worker-node UDS; return the target's JSON output.

    The platform builds the target's CloudEvent from *payload* (funcd ADR-0134): an object with a
    top-level ``"data"`` or ``"specversion"`` key is taken as a full envelope, so the target's
    ``event["data"]`` is only its ``data`` value, without the other keys. Send an object that has its
    own ``data`` key as ``{"specversion": "1.0", "data": payload}``.

    Raises ``RuntimeError`` on a non-2xx (no link → 403, unknown target → 404, bad input → 422,
    target down/timeout → 503) or when the socket is unavailable.

    Inside an invocation the call sends a ``traceparent`` and, when *channel* is set, writes one CLIENT
    span ``call <alias>`` on it once the call settles (ADR-0165).
    """
    # The CLIENT span brackets the whole call, so it opens before any check that can fail.
    with ClientSpan(channel, alias, member) as span:
        socket_path = os.environ.get("FUNCD_INVOKE_SOCKET")
        if not socket_path:
            raise RuntimeError(
                "context.invoke: worker-node local API socket unavailable (FUNCD_INVOKE_SOCKET unset)"
            )
        conn = _UnixHTTPConnection(socket_path)
        try:
            body = json.dumps(payload).encode("utf-8")
            headers = {"content-type": "application/json"}
            if member:
                headers[MEMBER_HEADER] = member
            if span.traceparent:
                headers["traceparent"] = span.traceparent
            conn.request("POST", f"/invoke/{alias}", body=body, headers=headers)
            resp = conn.getresponse()
            span.reply(resp.status)
            text = resp.read().decode("utf-8")
            if 200 <= resp.status < 300:
                return json.loads(text) if text else None
            raise RuntimeError(f'context.invoke("{alias}") failed: {resp.status} {text}')
        finally:
            conn.close()
