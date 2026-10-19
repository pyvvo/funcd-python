"""``context.secret``: the keys of a function's bound Secrets (funcd ADR-0245), from ``FUNCD_SECRET_KEYS``."""

from __future__ import annotations

from collections.abc import Mapping

from .config import listed_keys, listed_value


class SecretClient:
    """A function's bound secrets: an unbound key reads as None."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env

    def get(self, key: str) -> str | None:
        return listed_value(self._env, "FUNCD_SECRET_KEYS", key)

    def list(self) -> list[str]:
        return listed_keys(self._env, "FUNCD_SECRET_KEYS")
