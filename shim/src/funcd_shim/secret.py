"""``context.secret``: the keys and values of a function's bound Secrets, read from funcd over the worker-node
local API on every call (funcd ADR-0249): ``GET /secret`` and ``GET /secret/{key}``, checked by funcd, with no
cache. No secret sits in the worker's environment."""

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

    def _get(self, verb: str, path: str, *, missing_ok: bool) -> bytes | None:
        """GET path; a 404 is None when missing_ok, and any other non-200 raises, as the KV client does."""
        socket_path = os.environ.get("FUNCD_INVOKE_SOCKET")
        if not socket_path:
            raise RuntimeError(
                "context.secret: worker-node local API socket unavailable (FUNCD_INVOKE_SOCKET unset)"
            )
        conn = _UnixHTTPConnection(socket_path)
        try:
            conn.request("GET", path, headers=self._headers)
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        if resp.status == 404 and missing_ok:
            return None
        if resp.status != 200:
            raise RuntimeError(
                f"context.secret.{verb} failed: {resp.status} {data.decode('utf-8', 'replace')}"
            )
        return data

    def get(self, key: str) -> str | None:
        data = self._get("get", f"/secret/{quote(key, safe='')}", missing_ok=True)
        return None if data is None else cast("str", json.loads(data)["value"])

    def list(self) -> list[str]:
        data = cast("bytes", self._get("list", "/secret", missing_ok=False))
        return cast("list[str]", json.loads(data)["keys"])
