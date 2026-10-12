"""A context.sql over an in-memory SQLite: a batch runs in one transaction, as rqlite runs it."""

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any


class FakeSQL:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.db.execute("PRAGMA foreign_keys = ON")

    def _run(self, sql: str, params: Any = None) -> dict[str, Any]:
        cur = self.db.execute(sql, params if isinstance(params, Mapping) else list(params or []))
        if cur.description:
            return {"columns": [d[0] for d in cur.description], "values": [list(r) for r in cur.fetchall()]}
        return {"rowsAffected": cur.rowcount, "lastInsertId": cur.lastrowid}

    def query(self, alias: str, sql: str, params: Any = None) -> list[dict[str, Any]]:
        res = self._run(sql, params)
        return [dict(zip(res["columns"], v, strict=True)) for v in res["values"]]

    def query_values(self, alias: str, sql: str, params: Any = None) -> dict[str, Any]:
        return self._run(sql, params)

    def execute(self, alias: str, stmts: str | Sequence[Any], params: Any = None) -> Any:
        if isinstance(stmts, str):
            return self._run(stmts, params)
        self.db.execute("BEGIN")
        try:
            out = [self._run(s) if isinstance(s, str) else self._run(s[0], s[1]) for s in stmts]
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")
        return out


class FakeContext:
    def __init__(self) -> None:
        self.sql = FakeSQL()

    def log(self, *args: object) -> None:
        pass
