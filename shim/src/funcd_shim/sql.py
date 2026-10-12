"""Function-facing SQL over the worker-node local API (funcd ADR-0233).

Dials the per-sandbox socket (``FUNCD_INVOKE_SOCKET``); the platform routes ``/sql/…`` to the
PDP-authorized facade with the sandbox's identity, and the database's engine and credential never reach the
sandbox. ``query`` runs read-only; ``execute`` runs its statements in one transaction, all or nothing.
Stdlib-only, synchronous.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import quote

from .invoke import MEMBER_HEADER, _UnixHTTPConnection

type SQLParam = str | int | float | bool | None
"""A SQL parameter: rqlite takes JSON scalars."""

type SQLParams = Sequence[SQLParam] | Mapping[str, SQLParam]
"""Positional parameters for ``?``, or one mapping for ``:name``."""

type SQLStatement = str | tuple[str, SQLParams] | list[Any]
"""One statement of a batch: its SQL text, alone or with its parameters."""


def _wire(sql: str, params: SQLParams | None) -> list[Any]:
    if params is None:
        return [sql]
    if isinstance(params, Mapping):
        return [sql, dict(params)]
    return [sql, *params]


def _statement(s: SQLStatement) -> list[Any]:
    if isinstance(s, str):
        return [s]
    sql, *rest = s
    return _wire(sql, rest[0] if rest else None)


class SQLClient:
    """The SQL databases a function binds (``spec.sql``), by alias."""

    def __init__(self, member: str | None = None) -> None:
        # In a pool, every request names the calling member; funcd checks it against the pool.
        self._headers = {"Content-Type": "application/json"}
        if member:
            self._headers[MEMBER_HEADER] = member

    def _call(self, verb: str, alias: str, statements: list[list[Any]]) -> list[dict[str, Any]]:
        socket_path = os.environ.get("FUNCD_INVOKE_SOCKET")
        if not socket_path:
            raise RuntimeError(
                "context.sql: worker-node local API socket unavailable (FUNCD_INVOKE_SOCKET unset)"
            )
        conn = _UnixHTTPConnection(socket_path)
        try:
            body = json.dumps({"statements": statements}).encode("utf-8")
            conn.request("POST", f"/sql/{quote(alias, safe='')}/{verb}", body=body, headers=self._headers)
            resp = conn.getresponse()
            data = resp.read()
        finally:
            conn.close()
        if not 200 <= resp.status < 300:
            raise RuntimeError(f"context.sql.{verb} failed: {resp.status} {data.decode('utf-8', 'replace')}")
        return cast("list[dict[str, Any]]", json.loads(data)["results"])

    def query(self, alias: str, sql: str, params: SQLParams | None = None) -> list[dict[str, Any]]:
        """Rows as dicts; with a duplicate column name the last one wins (``query_values`` keeps every
        column)."""
        res = self.query_values(alias, sql, params)
        return [dict(zip(res["columns"], row, strict=True)) for row in res["values"]]

    def query_values(self, alias: str, sql: str, params: SQLParams | None = None) -> dict[str, Any]:
        """Rows as ``{"columns": [...], "values": [[...]]}``, as rqlite returns them: every column of a join
        is kept."""
        return self._call("query", alias, [_wire(sql, params)])[0]

    def execute(
        self, alias: str, sql_or_statements: str | Sequence[SQLStatement], params: SQLParams | None = None
    ) -> Any:
        """One statement's ``{"rowsAffected", "lastInsertId"}`` (with ``columns`` and ``values`` for
        RETURNING), or the list of them for a batch of statements, which runs all or nothing."""
        if isinstance(sql_or_statements, str):
            return self._call("execute", alias, [_wire(sql_or_statements, params)])[0]
        return self._call("execute", alias, [_statement(s) for s in sql_or_statements])
