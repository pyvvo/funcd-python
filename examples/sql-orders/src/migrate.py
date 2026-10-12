"""The migrate function: it applies the migrations of migrations_sql that table _migrations lacks, each with
its _migrations row, in one context.sql call, so a failed migration records nothing (funcd ADR-0236 Decision
5). Run it after each deploy, or as an App's preApply hook."""

from typing import TypedDict

from funcd_shim import CloudEvent, FunctionContext

from migrations_sql import MIGRATIONS
from sqlrun import ALIAS


class FuncOutput(TypedDict):
    applied: int


def handle(context: FunctionContext, event: CloudEvent[object]) -> FuncOutput:
    context.sql.execute(ALIAS, "CREATE TABLE IF NOT EXISTS _migrations (version TEXT PRIMARY KEY)")
    done = {r["version"] for r in context.sql.query(ALIAS, "SELECT version FROM _migrations")}
    batch: list[str | tuple[str, list[str]]] = []
    pending = [v for v in MIGRATIONS if v not in done]
    for version in pending:
        batch += MIGRATIONS[version]
        batch.append(("INSERT INTO _migrations (version) VALUES (?)", [version]))
    if batch:
        context.sql.execute(ALIAS, batch)
    context.log("applied", len(pending), "migrations")
    return {"applied": len(pending)}
