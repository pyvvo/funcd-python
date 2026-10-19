"""Step summarize of Workflow readings: it reads what ingest stored and returns the sensor's statistics and
its latest values with a moving average and a rank, computed in the engine by a CTE and window functions."""

from typing import TypedDict

from funcd_shim import CloudEvent, FunctionContext
from readings_core.tables import readings, rows
from sqlalchemy import func, select


class FuncInput(TypedDict):
    sensor: str
    stored: int


class Point(TypedDict):
    seq: int
    value: float
    moving_avg: float
    rank: int


class FuncOutput(TypedDict):
    sensor: str
    count: int
    mean: float
    low: float
    high: float
    latest: list[Point]


def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    sensor = event["data"]["sensor"]
    mine = select(readings.c.seq, readings.c.value).where(readings.c.sensor == sensor).cte("mine")
    stats = rows(
        context,
        select(
            func.count().label("count"),
            func.avg(mine.c.value).label("mean"),
            func.min(mine.c.value).label("low"),
            func.max(mine.c.value).label("high"),
        ),
    )[0]
    windowed = select(
        mine.c.seq,
        mine.c.value,
        func.avg(mine.c.value).over(order_by=mine.c.seq, rows=(-2, 0)).label("moving_avg"),
        func.rank().over(order_by=mine.c.value.desc()).label("rank"),
    ).subquery()
    latest = rows(context, select(windowed).order_by(windowed.c.seq.desc()).limit(3))
    return {
        "sensor": sensor,
        "count": int(stats["count"]),
        "mean": float(stats["mean"]),
        "low": float(stats["low"]),
        "high": float(stats["high"]),
        "latest": [
            {
                "seq": int(r["seq"]),
                "value": float(r["value"]),
                "moving_avg": float(r["moving_avg"]),
                "rank": int(r["rank"]),
            }
            for r in reversed(latest)
        ],
    }
