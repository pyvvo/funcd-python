"""Step ingest of Workflow readings: it stores a batch of one sensor's values after the ones it already holds,
the table created on first use, all in one call."""

from typing import TypedDict

from funcd_shim import CloudEvent, FunctionContext
from sqlalchemy import func, insert, select

from tables import execute, readings, rows, schema


class FuncInput(TypedDict):
    sensor: str
    values: list[float]


class FuncOutput(TypedDict):
    sensor: str
    stored: int


def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    data = event["data"]
    execute(context, schema())
    last = rows(
        context,
        select(func.coalesce(func.max(readings.c.seq), 0).label("seq")).where(
            readings.c.sensor == data["sensor"]
        ),
    )
    start = int(last[0]["seq"]) + 1
    execute(
        context,
        [
            insert(readings).values(sensor=data["sensor"], seq=start + i, value=v)
            for i, v in enumerate(data["values"])
        ],
    )
    return {"sensor": data["sensor"], "stored": len(data["values"])}
