"""The audit function: a Sensor calls it with each change of table orders that the change feed of orders-db
delivers (funcd ADR-0235), and it records the change in table order_events."""

from typing import Any, TypedDict

from funcd_shim import CloudEvent, FunctionContext
from sqlmodel import insert

from models import OrderEvent
from sqlrun import execute


class Change(TypedDict, total=False):
    op: str
    table: str
    rowId: int
    before: dict[str, Any]
    after: dict[str, Any]


class FuncOutput(TypedDict):
    recorded: int


def handle(context: FunctionContext, event: CloudEvent[Change]) -> FuncOutput:
    change = event.get("data") or {}
    row = change.get("after") or change.get("before") or {}
    execute(
        context,
        insert(OrderEvent).values(
            order_id=change.get("rowId", 0), change=change.get("op", ""), status=row.get("status")
        ),
    )
    return {"recorded": 1}
