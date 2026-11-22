"""Function-facing calls of an add-on object's engine over the worker-node local API (funcd ADR-0338).

Dials the per-sandbox socket (``FUNCD_INVOKE_SOCKET``); funcd checks the binding, Cedar's ``addon::call``
and the path as the object's edge entry does, then forwards the request to the engine. Every answer of the
engine carries ``X-Funcd-Addon-Reply`` and is returned whatever its status; a refusal of funcd carries none
and raises. Stdlib-only, synchronous.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from .invoke import MEMBER_HEADER, _UnixHTTPConnection
from .tracespan import ClientSpan

if TYPE_CHECKING:
    from .funclog import Channel

ADDON_REPLY_HEADER = "x-funcd-addon-reply"
"""The local API header that marks an answer as the engine's; a call that funcd refused carries none."""


@dataclass(frozen=True)
class AddonResponse:
    """An answer of an add-on object's engine: its status, its headers in the engine's order with their
    names in lower case, so a repeated ``set-cookie`` stays apart, and its body."""

    status: int
    headers: list[tuple[str, str]]
    body: bytes


class AddonClient:
    """The add-on objects that a function binds (``spec.bindings.addon``), by alias."""

    def __init__(self, member: str | None = None, channel: Channel | None = None) -> None:
        # In a pool, every request names the calling member; funcd checks it against the pool.
        self._member = member
        self._channel = channel

    def fetch(
        self,
        alias: str,
        path: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> AddonResponse:
        """Sends an HTTP request to the engine of the add-on object that the function binds under
        ``alias``, through funcd, and returns the engine's answer whatever its status; funcd follows no
        redirect. ``path`` starts with ``/`` and may carry a query, and ``method``, ``headers`` and ``body``
        make the rest of the request. Raises ``RuntimeError`` with funcd's problem when funcd refuses the
        call: 403 for an alias the function does not bind or a call a Policy forbids, 404 for a path the
        object's edge entry does not serve, 413 for a request or an answer body over
        ``addons.callPayloadLimit``, 1 MiB by default, and 503 while the object is not Ready, while its
        namespace is held (with its ``Retry-After``), or when the engine does not answer within
        ``addons.callTimeout``, 10 seconds by default."""
        with ClientSpan(self._channel, alias, self._member) as span:
            socket_path = os.environ.get("FUNCD_INVOKE_SOCKET")
            if not socket_path:
                raise RuntimeError(
                    "context.addon: worker-node local API socket unavailable (FUNCD_INVOKE_SOCKET unset)"
                )
            if not path.startswith("/"):
                raise ValueError(f'context.addon.fetch("{alias}"): path "{path}" must start with "/"')
            own: dict[str, str] = {}
            if self._member:
                own[MEMBER_HEADER] = self._member
            if span.traceparent:
                own["traceparent"] = span.traceparent
            names = {name.lower() for name in own}
            sent = {k: v for k, v in (headers or {}).items() if k.lower() not in names} | own
            conn = _UnixHTTPConnection(socket_path)
            try:
                conn.request(method, f"/addon/{quote(alias, safe='')}{path}", body=body, headers=sent)
                resp = conn.getresponse()
                span.reply(resp.status)
                data = resp.read()
                got = [(name.lower(), value) for name, value in resp.getheaders()]
            finally:
                conn.close()
            if all(name != ADDON_REPLY_HEADER for name, _ in got):
                raise RuntimeError(_refusal(alias, resp.status, got, data))
            if resp.status >= 500:
                span.failed()
            return AddonResponse(resp.status, [h for h in got if h[0] != ADDON_REPLY_HEADER], data)


def _refusal(alias: str, status: int, headers: list[tuple[str, str]], body: bytes) -> str:
    """The message of a funcd refusal: its status and problem, and a 503's Retry-After."""
    text = f'context.addon.fetch("{alias}") failed: {status} {body.decode("utf-8", "replace")}'
    after = next((value for name, value in headers if name == "retry-after"), None)
    if status == 503 and after:
        text += f" (Retry-After: {after})"
    return text
