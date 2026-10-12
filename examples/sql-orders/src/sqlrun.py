"""SQLModel statements sent through context.sql (funcd ADR-0233): compiled with the SQLite dialect into SQL
text and positional parameters, so no driver or credential is needed in the sandbox."""

from typing import Any, cast

from funcd_shim import FunctionContext
from sqlalchemy.dialects import sqlite
from sqlalchemy.sql import ClauseElement
from sqlalchemy.sql.compiler import SQLCompiler

ALIAS = "app"


def compiled(stmt: ClauseElement) -> tuple[str, list[Any]]:
    c = cast(SQLCompiler, stmt.compile(dialect=sqlite.dialect()))
    return str(c), [c.params[name] for name in c.positiontup or []]


def rows(context: FunctionContext, stmt: ClauseElement) -> list[dict[str, Any]]:
    text, params = compiled(stmt)
    return context.sql.query(ALIAS, text, params)


def execute(context: FunctionContext, *stmts: ClauseElement) -> list[dict[str, Any]]:
    """Runs the statements in one call: rqlite applies all of them or none."""
    return cast(list[dict[str, Any]], context.sql.execute(ALIAS, [compiled(s) for s in stmts]))
