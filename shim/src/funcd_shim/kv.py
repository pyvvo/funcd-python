"""Function-facing KV over the worker-node local API (HTTP-over-UDS, ADR-0069).

Dials the same per-sandbox socket as ``context.invoke`` (``FUNCD_INVOKE_SOCKET``); the platform routes
``/kv/…`` to the PDP-authorized Facade with the sandbox's namespace identity. Stdlib-only.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
from dataclasses import dataclass
from urllib.parse import quote

from .invoke import MEMBER_HEADER


class _UnixHTTPConnection(http.client.HTTPConnection):
    """An HTTPConnection that dials a Unix domain socket instead of TCP."""

    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self._socket_path)
        self.sock = sock


def _conn() -> _UnixHTTPConnection:
    socket_path = os.environ.get("FUNCD_INVOKE_SOCKET")
    if not socket_path:
        raise RuntimeError("context.kv: worker-node local API socket unavailable (FUNCD_INVOKE_SOCKET unset)")
    return _UnixHTTPConnection(socket_path)


def _key_path(binding: str, key: str) -> str:
    # key may be hierarchical ("a/b"); keep the "/" separators (the server's {key...} captures them).
    segs = "/".join(quote(s, safe="") for s in key.split("/"))
    return f"/kv/{quote(binding, safe='')}/{segs}"


def _with_ttl(path: str, ttl: int | None) -> str:
    if ttl is None:
        return path
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 1:
        raise ValueError(f"context.kv: ttl must be a whole number of seconds from 1, not {ttl!r}")
    return f"{path}?ttl={ttl}"


def _version(etag: str | None) -> str:
    return (etag or "").strip('"')


@dataclass(frozen=True)
class KVEntry:
    """A key's value with its version, which a ``compare_and_set`` or a ``delete_if`` names."""

    value: bytes
    version: str


@dataclass(frozen=True)
class _Reply:
    status: int
    etag: str | None
    data: bytes


class KVClient:
    """The key-value tables that a function binds, each by its alias in spec.bindings.kv.

    ``put_if_absent``, ``compare_and_set`` and ``delete_if`` write only while their condition holds, and say
    who won: of racing writers exactly one gets the new version, the others ``None`` (``False`` for a delete).
    A key written with ``ttl`` (whole seconds) lives at least that long from when funcd takes the write,
    so a holder that counts its lease from before the call never counts past the key's expiry. Each
    write sets its own expiry: one without ``ttl`` leaves the key with none, so a ``compare_and_set``
    renews a lease only with a new ``ttl``.
    """

    def __init__(self, member: str | None = None) -> None:
        # In a pool, every request names the calling member; funcd checks it against the pool.
        self._headers = {MEMBER_HEADER: member} if member else {}

    def _send(
        self,
        verb: str,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        expect: int = 0,
        headers: dict[str, str] | None = None,
    ) -> _Reply:
        """One request; a reply that is neither 2xx nor ``expect`` raises."""
        conn = _conn()
        try:
            conn.request(method, path, body=body, headers={**self._headers, **(headers or {})})
            resp = conn.getresponse()
            reply = _Reply(resp.status, resp.getheader("ETag"), resp.read())
        finally:
            conn.close()
        if reply.status != expect and not 200 <= reply.status < 300:
            raise RuntimeError(
                f"context.kv.{verb} failed: {reply.status} {reply.data.decode('utf-8', 'replace')}"
            )
        return reply

    def get(self, binding: str, key: str) -> bytes | None:
        """The key's value as bytes, or ``None`` when the key does not exist."""
        entry = self.get_entry(binding, key)
        return None if entry is None else entry.value

    def get_entry(self, binding: str, key: str) -> KVEntry | None:
        """The key's value with its version, or ``None`` when the key does not exist."""
        reply = self._send("get", "GET", _key_path(binding, key), expect=404)
        return None if reply.status == 404 else KVEntry(reply.data, _version(reply.etag))

    def get_str(self, binding: str, key: str) -> str | None:
        """The key's value decoded as UTF-8 text, or ``None`` when the key does not exist."""
        value = self.get(binding, key)
        return None if value is None else value.decode("utf-8")

    def get_json(self, binding: str, key: str) -> object | None:
        """The key's value parsed as JSON, or ``None`` when the key does not exist. Write it with
        ``put(binding, key, json.dumps(value))``."""
        text = self.get_str(binding, key)
        return None if text is None else json.loads(text)

    def put(self, binding: str, key: str, value: bytes | str, *, ttl: int | None = None) -> None:
        """Writes the value. ``ttl``, in whole seconds, sets the key's expiry; a put without it leaves
        the key with none. Only the table's owner can write."""
        body = value.encode("utf-8") if isinstance(value, str) else value
        self._send("put", "PUT", _with_ttl(_key_path(binding, key), ttl), body=body)

    def put_if_absent(
        self, binding: str, key: str, value: bytes | str, *, ttl: int | None = None
    ) -> str | None:
        """Creates the key: its new version, or ``None`` when it exists (an expired key does not)."""
        return self._put("put_if_absent", binding, key, value, ttl, {"If-None-Match": "*"})

    def compare_and_set(
        self, binding: str, key: str, value: bytes | str, version: str, *, ttl: int | None = None
    ) -> str | None:
        """Replaces the key while it is at ``version``: its new version, or ``None`` when it is not."""
        return self._put("compare_and_set", binding, key, value, ttl, {"If-Match": f'"{version}"'})

    def _put(
        self, verb: str, binding: str, key: str, value: bytes | str, ttl: int | None, headers: dict[str, str]
    ) -> str | None:
        body = value.encode("utf-8") if isinstance(value, str) else value
        reply = self._send(
            verb, "PUT", _with_ttl(_key_path(binding, key), ttl), body=body, expect=412, headers=headers
        )
        return None if reply.status == 412 else _version(reply.etag)

    def delete(self, binding: str, key: str) -> None:
        """Deletes the key."""
        self._send("delete", "DELETE", _key_path(binding, key))

    def delete_if(self, binding: str, key: str, version: str) -> bool:
        """Deletes the key while it is at ``version``, as a lease holder releases what it holds: ``True``
        when it did."""
        reply = self._send(
            "delete_if", "DELETE", _key_path(binding, key), expect=412, headers={"If-Match": f'"{version}"'}
        )
        return reply.status != 412

    def list(self, binding: str, prefix: str = "") -> list[str]:
        """The keys that start with ``prefix``; without a prefix, every key of the table."""
        path = f"/kv/{quote(binding, safe='')}"
        if prefix:
            path += f"?prefix={quote(prefix, safe='')}"
        text = self._send("list", "GET", path).data.decode("utf-8")
        return json.loads(text) if text else []
