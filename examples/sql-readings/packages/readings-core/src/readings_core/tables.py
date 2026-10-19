"""The readings table, with SQLAlchemy Core: no ORM, the statements are built from the Table and compiled with
the SQLite dialect into SQL text and parameters for context.sql (funcd ADR-0233)."""

from typing import Any, cast

from funcd_shim import FunctionContext
from sqlalchemy import Column, Float, Integer, MetaData, String, Table
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable, Index
from sqlalchemy.sql import ClauseElement
from sqlalchemy.sql.compiler import SQLCompiler

ALIAS = "app"

metadata = MetaData()
readings = Table(
    "readings",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("sensor", String, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("value", Float, nullable=False),
)
by_sensor = Index("readings_sensor_seq", readings.c.sensor, readings.c.seq)


def schema() -> list[ClauseElement]:
    return [CreateTable(readings, if_not_exists=True), CreateIndex(by_sensor, if_not_exists=True)]


def compiled(stmt: ClauseElement) -> tuple[str, list[Any]]:
    c = cast(SQLCompiler, stmt.compile(dialect=sqlite.dialect()))
    return str(c), [c.params[name] for name in getattr(c, "positiontup", None) or []]


def execute(context: FunctionContext, stmts: list[ClauseElement]) -> list[dict[str, Any]]:
    """Runs the statements in one call: rqlite applies all of them or none."""
    return cast(list[dict[str, Any]], context.sql.execute(ALIAS, [compiled(s) for s in stmts]))


def rows(context: FunctionContext, stmt: ClauseElement) -> list[dict[str, Any]]:
    text, params = compiled(stmt)
    return context.sql.query(ALIAS, text, params)
