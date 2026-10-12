"""The orders database, code first: Alembic's migrations (alembic/versions) build these tables, and the
functions query them with SQLModel."""

from sqlmodel import Field, SQLModel


class Customer(SQLModel, table=True):
    __tablename__ = "customers"
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True)


class Order(SQLModel, table=True):
    __tablename__ = "orders"
    id: int | None = Field(default=None, primary_key=True)
    customer_id: int = Field(foreign_key="customers.id")
    item: str
    quantity: int
    status: str = "placed"
    note: str | None = None


class OrderEvent(SQLModel, table=True):
    """One change of an order, as the audit function records it from the change feed, with the commit index
    that orders it: the Sensor's calls can finish in any order."""

    __tablename__ = "order_events"
    id: int | None = Field(default=None, primary_key=True)
    order_id: int
    change_index: int
    change: str
    status: str | None = None
