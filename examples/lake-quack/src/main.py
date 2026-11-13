"""lake-reader: runs the SQL of each call on the Lake that the Function binds, and returns the rows.

A Quack client is a DuckDB database with the quack extension. The handler reads the address of the Lake
gateway and the Function's token from FUNCD_LAKE_<ALIAS>_URL and FUNCD_LAKE_<ALIAS>_TOKEN, which funcd sets
for each binding under spec.bindings.lake, and runs the SQL there with quack_query.

The duckdb package and the quack and httpfs extensions travel in the Function's bundle, which
`uv run funcd-bundle` builds into dist/lake-reader/.
"""

from __future__ import annotations

import os
from typing import TypedDict

import duckdb  # the Quack client; the Function's bundle carries it
from funcd_shim import CloudEvent, FunctionContext


class FuncInput(TypedDict, total=False):
    """The event payload: the SQL to run, and which lake alias to run it against."""

    sql: str
    lake: str  # the spec.bindings.lake alias (defaults to "lake")


class FuncOutput(TypedDict):
    """The 200 body: the query's rows (each a list of column values)."""

    rows: list[list[object]]


def _quack_uri(endpoint: str) -> str:
    """Normalize a lake endpoint to a quack:// RPC URI."""
    return endpoint if endpoint.startswith("quack://") else "quack://" + endpoint


# region handler
def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    """Run the requested SQL on the bound lake over Quack and return the rows."""
    data = event.get("data") or {}
    alias = (data.get("lake") or "lake").upper()
    sql = data.get("sql") or "SELECT 42 AS answer"

    # Set by funcd for the binding: the Lake gateway's address and the Function's token.
    url = os.environ[f"FUNCD_LAKE_{alias}_URL"]
    token = os.environ[f"FUNCD_LAKE_{alias}_TOKEN"]

    con = duckdb.connect()
    # Load the extensions offline: from DUCKDB_EXTENSION_DIRECTORY when it is set, as under funcdctl dev,
    # else from the duckdb-ext folder of the bundle that FUNCD_BUNDLE_DIR names, else DuckDB's default.
    bundle_dir = os.environ.get("FUNCD_BUNDLE_DIR")
    ext_dir = os.environ.get("DUCKDB_EXTENSION_DIRECTORY")
    if not ext_dir and bundle_dir:
        candidate = os.path.join(bundle_dir, "duckdb-ext")
        if os.path.isdir(candidate):
            ext_dir = candidate
    if ext_dir:
        con.execute("SET autoinstall_known_extensions=false")
        con.execute("SET autoload_known_extensions=false")
        con.execute(f"SET extension_directories=['{ext_dir}']")
    con.execute("LOAD quack")
    rows = con.execute(
        "SELECT * FROM quack_query(?, ?, token := ?, disable_ssl := true)",
        [_quack_uri(url), sql, token],
    ).fetchall()
    context.log("lake-quack", alias, len(rows))
    return {"rows": [list(r) for r in rows]}


# endregion handler
