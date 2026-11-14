"""Function-facing blob over the worker-node local API (HTTP-over-UDS, ADR-0127).

Dials the same per-sandbox socket as ``context.kv``/``context.invoke`` (``FUNCD_INVOKE_SOCKET``); the
platform routes ``/blob/…`` to the binding-gated, PDP-authorized Facade with the sandbox's function
identity (bind-as-grant on ``spec.bindings.blob``). Stdlib-only — no boto3, no keypair. The blob twin of
``context.kv``; v1 is bytes-in-memory (streaming is a v2 follow-up).
"""

from __future__ import annotations

import http.client
import json
import os
import socket
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
        raise RuntimeError(
            "context.blob: worker-node local API socket unavailable (FUNCD_INVOKE_SOCKET unset)"
        )
    return _UnixHTTPConnection(socket_path)


def _key_path(binding: str, key: str) -> str:
    # key may be hierarchical ("a/b"); keep the "/" separators (the server's {key...} captures them).
    segs = "/".join(quote(s, safe="") for s in key.split("/"))
    return f"/blob/{quote(binding, safe='')}/{segs}"


class BlobClient:
    """The Bucket prefixes that a function binds, each by its alias in ``spec.bindings.blob``: read, write,
    delete and list their objects, whose keys are relative to the binding's prefix."""

    def __init__(self, member: str | None = None) -> None:
        # In a pool, every request names the calling member; funcd checks it against the pool.
        self._headers = {MEMBER_HEADER: member} if member else {}

    def get(self, binding: str, key: str) -> bytes | None:
        """The object's content as bytes, or ``None`` when the object does not exist."""
        conn = _conn()
        try:
            conn.request("GET", _key_path(binding, key), headers=self._headers)
            resp = conn.getresponse()
            data = resp.read()
            if resp.status == 404:
                return None
            if not 200 <= resp.status < 300:
                text = data.decode("utf-8", "replace")
                raise RuntimeError(f"context.blob.get failed: {resp.status} {text}")
            return data
        finally:
            conn.close()

    def put(self, binding: str, key: str, data: bytes) -> None:
        """Writes the object, replacing any object with that key. ``data`` is at most 64 MiB, and at most
        the Bucket's ``spec.quota.maxObjectBytes`` when that is set: a larger one fails with the HTTP status
        413. Only the prefix's owner and the writers that a RolesAssignment names can write."""
        conn = _conn()
        try:
            conn.request("PUT", _key_path(binding, key), body=data, headers=self._headers)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8", "replace")
            if not 200 <= resp.status < 300:
                raise RuntimeError(f"context.blob.put failed: {resp.status} {text}")
        finally:
            conn.close()

    def delete(self, binding: str, key: str) -> None:
        """Deletes the object, and fails with the HTTP status 404 when it does not exist. Only the prefix's
        owner and the writers that a RolesAssignment names can delete."""
        conn = _conn()
        try:
            conn.request("DELETE", _key_path(binding, key), headers=self._headers)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8", "replace")
            if not 200 <= resp.status < 300:
                raise RuntimeError(f"context.blob.delete failed: {resp.status} {text}")
        finally:
            conn.close()

    def list(self, binding: str, prefix: str = "") -> list[str]:
        """The keys that start with ``prefix``; without a prefix, every key under the binding's prefix."""
        path = f"/blob/{quote(binding, safe='')}"
        if prefix:
            path += f"?prefix={quote(prefix, safe='')}"
        conn = _conn()
        try:
            conn.request("GET", path, headers=self._headers)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8")
            if not 200 <= resp.status < 300:
                raise RuntimeError(f"context.blob.list failed: {resp.status} {text}")
            return json.loads(text) if text else []
        finally:
            conn.close()

    def signed_url(self, binding: str, key: str, method: str = "GET", expiry: str | None = None) -> str:
        """A presigned URL that lets its holder send a ``method`` request for the object until ``expiry``
        passes: a duration string such as ``"10m"`` or ``"1h30m"``, in whole seconds from 1s to 168h, or 15
        minutes when ``None``. A method other than ``GET``, ``PUT`` or ``DELETE``, or any other expiry, ``""``
        included, fails with the HTTP status 400; an ``expiry`` that is not a string raises ``TypeError``
        before any request. A ``PUT`` or ``DELETE`` URL needs permission to write the prefix, and a ``PUT``
        URL fails with the HTTP status 403 when the Bucket sets ``maxObjectBytes`` or a size quota applies to
        the Bucket or the prefix, since funcd could not enforce it on the upload. No blob store that funcd
        opens signs URLs, so a call that passes these checks fails with the HTTP status 503."""
        if expiry is not None and not isinstance(expiry, str):
            raise TypeError(
                "context.blob.signed_url: expiry must be a duration string such as 10m or 1h30m, "
                f"not {type(expiry).__name__}"
            )
        path = f"{_key_path(binding, key)}?sign=1&method={quote(method, safe='')}"
        if expiry is not None:
            path += f"&expiry={quote(expiry, safe='')}"
        conn = _conn()
        try:
            conn.request("GET", path, headers=self._headers)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8", "replace")
            if not 200 <= resp.status < 300:
                raise RuntimeError(f"context.blob.signed_url failed: {resp.status} {text}")
            return text
        finally:
            conn.close()
