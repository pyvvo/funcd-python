"""``context.secret``: the keys and values of a function's bound Secrets, read from funcd over the worker-node
local API on every call (funcd ADR-0249): ``GET /secret`` and ``GET /secret/{key}``, checked by funcd, with no
cache. No secret sits in the worker's environment. A function writes one key of a bound Secret it may write,
as its owner or through a writer-role grant, with ``PUT`` and ``DELETE /secret/{binding}/{key}`` (funcd
ADR-0327)."""

from __future__ import annotations

import json
import os
from typing import cast
from urllib.parse import quote

from .invoke import MEMBER_HEADER, _UnixHTTPConnection


class SecretClient:
    """A function's bound secrets: an unbound key reads as None."""

    def __init__(self, member: str | None = None) -> None:
        # In a pool, every request names the calling member; funcd checks it against the pool.
        self._headers = {MEMBER_HEADER: member} if member else {}

    def _request(self, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
        """One request on the local API socket: its status and body."""
        socket_path = os.environ.get("FUNCD_INVOKE_SOCKET")
        if not socket_path:
            raise RuntimeError(
                "context.secret: worker-node local API socket unavailable (FUNCD_INVOKE_SOCKET unset)"
            )
        headers = dict(self._headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn = _UnixHTTPConnection(socket_path)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        return resp.status, data

    def _get(self, verb: str, path: str, *, missing_ok: bool) -> bytes | None:
        """GET path; a 404 is None when missing_ok, and any other non-200 raises, as the KV client does."""
        status, data = self._request("GET", path)
        if status == 404 and missing_ok:
            return None
        if status != 200:
            raise RuntimeError(f"context.secret.{verb} failed: {status} {data.decode('utf-8', 'replace')}")
        return data

    def _write(self, verb: str, method: str, binding: str, key: str, body: bytes | None = None) -> None:
        """A write of one key of Secret binding; any status but 2xx raises, as the KV client does."""
        status, data = self._request(method, f"/secret/{quote(binding, safe='')}/{quote(key, safe='')}", body)
        if not 200 <= status < 300:
            raise RuntimeError(f"context.secret.{verb} failed: {status} {data.decode('utf-8', 'replace')}")

    def get(self, key: str) -> str | None:
        data = self._get("get", f"/secret/{quote(key, safe='')}", missing_ok=True)
        return None if data is None else cast("str", json.loads(data)["value"])

    def list(self) -> list[str]:
        data = cast("bytes", self._get("list", "/secret", missing_ok=False))
        return cast("list[str]", json.loads(data)["keys"])

    def put(self, binding: str, key: str, value: str) -> None:
        """Writes key in Secret ``binding``, which the function binds and may write: its owner, or a principal
        a writer role names. Every function that binds it reads the value at its next read."""
        self._write("put", "PUT", binding, key, json.dumps({"value": value}).encode())

    def delete(self, binding: str, key: str) -> None:
        """Removes key from Secret ``binding``, as put may write it; a key the Secret lacks is no error."""
        self._write("delete", "DELETE", binding, key)
