"""The orders function, the owner of orders-db: SQLModel statements over context.sql (sqlrun)."""

from typing import Any, TypedDict

from funcd_shim import CloudEvent, FunctionContext
from sqlmodel import col, insert, select, update

from models import Customer, Order
from sqlrun import execute, rows


class FuncInput(TypedDict, total=False):
    op: str
    email: str
    item: str
    quantity: int
    id: int
    note: str


class OrderOut(TypedDict):
    id: int
    email: str
    item: str
    quantity: int
    status: str
    note: str | None


class FuncOutput(TypedDict, total=False):
    orders: list[OrderOut]
    changed: int


def _place(context: FunctionContext, data: FuncInput) -> FuncOutput:
    """Adds the customer on its first order and the order itself in one call: both or neither."""
    email = data.get("email", "")
    customer = select(Customer.id).where(Customer.email == email).scalar_subquery()
    execute(
        context,
        insert(Customer).values(email=email).prefix_with("OR IGNORE"),
        insert(Order).values(
            customer_id=customer, item=data.get("item", ""), quantity=data.get("quantity", 1), status="placed"
        ),
    )
    return {"changed": 1}


def _set(context: FunctionContext, order_id: int, **values: Any) -> FuncOutput:
    res = execute(context, update(Order).where(col(Order.id) == order_id).values(**values))
    return {"changed": int(res[0].get("rowsAffected", 0))}


def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    data = event.get("data") or {}
    match data.get("op", "list"):
        case "place":
            return _place(context, data)
        case "ship":
            return _set(context, data.get("id", 0), status="shipped")
        case "cancel":
            return _set(context, data.get("id", 0), status="cancelled")
        case "note":
            return _set(context, data.get("id", 0), note=data.get("note"))
    stmt = (
        select(Order, Customer.email)
        .join(Customer, col(Customer.id) == col(Order.customer_id))
        .order_by(col(Order.id))
    )
    if "email" in data:
        stmt = stmt.where(Customer.email == data["email"])
    found = rows(context, stmt)
    keys = ("id", "email", "item", "quantity", "status", "note")
    return {"orders": [OrderOut(**{k: r[k] for k in keys}) for r in found]}  # type: ignore[typeddict-item]
