"""``context.config``: the keys of a function's bound ConfigMaps (funcd ADR-0245).

funcd sets ``FUNCD_CONFIG_KEYS`` and ``FUNCD_SECRET_KEYS``, comma-separated lists of the bound keys of each
kind in the environment; an absent or empty list is no keys. The clients read the environment at call time, so
a pooled member reads its own copy (ADR-0158). ``context.secret`` reads its list with the same helpers.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


def listed_keys(env: Mapping[str, str] | None, var: str) -> list[str]:
    value = (os.environ if env is None else env).get(var)
    return value.split(",") if value else []


def listed_value(env: Mapping[str, str] | None, var: str, key: str) -> str | None:
    if key not in listed_keys(env, var):
        return None
    return (os.environ if env is None else env).get(key)


class ConfigClient:
    """A function's bound config: a key a secret overrides, or any unbound variable, reads as None."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env

    def get(self, key: str) -> str | None:
        return listed_value(self._env, "FUNCD_CONFIG_KEYS", key)

    def list(self) -> list[str]:
        return listed_keys(self._env, "FUNCD_CONFIG_KEYS")
