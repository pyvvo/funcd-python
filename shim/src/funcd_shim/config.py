"""``context.config``: the keys of a function's bound ConfigMaps (funcd ADR-0245).

funcd sets ``FUNCD_CONFIG_KEYS``, the comma-separated bound config keys in the environment; an absent or empty
list is no keys. The client reads the environment at call time, so a pooled member reads its own copy
(ADR-0158). Secrets are read from funcd instead (``context.secret``, funcd ADR-0249)."""

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
    """A function's bound config: the keys of the ConfigMaps in ``spec.bindings.config``, with the values that
    the worker got when it started. A key that starts with ``FUNCD_``, a key that a bound Secret also holds,
    and any other variable read as ``None``. While funcd's S3 gateway runs, the AWS key pair that funcd issues
    replaces a ConfigMap's values for ``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY``."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env

    def get(self, key: str) -> str | None:
        """The key's value, or ``None`` when ``list()`` does not include the key."""
        return listed_value(self._env, "FUNCD_CONFIG_KEYS", key)

    def list(self) -> list[str]:
        """The keys of the bound ConfigMaps, sorted, without the keys left out."""
        return listed_keys(self._env, "FUNCD_CONFIG_KEYS")
