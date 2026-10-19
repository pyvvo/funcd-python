"""``context.catalog``: a catalog binding's proxy URL and this function's token (funcd ADR-0245, ADR-0091,
ADR-0137), from ``FUNCD_CATALOG_<ALIAS>_URL`` and ``_TOKEN`` at call time."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TypedDict


class CatalogEndpoint(TypedDict):
    url: str
    token: str


class CatalogClient:
    """A function's catalog bindings by alias."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = env

    def get(self, alias: str) -> CatalogEndpoint:
        """Raises KeyError naming the alias when it is not bound."""
        env = os.environ if self._env is None else self._env
        prefix = f"FUNCD_CATALOG_{alias.upper()}_"
        url, token = env.get(prefix + "URL"), env.get(prefix + "TOKEN")
        if not url or not token:
            raise KeyError(f'context.catalog.get: catalog "{alias}" is not bound')
        return {"url": url, "token": token}
