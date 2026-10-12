from typing import Any, cast

import pytest
from fakesql import FakeContext
from funcd_shim import CloudEvent, FunctionContext

import audit
import migrate
import orders
from migrations_sql import MIGRATIONS


def _event(data: Any) -> CloudEvent[Any]:
    return cast(CloudEvent[Any], {"id": "1", "source": "/test", "type": "test", "data": data})


@pytest.fixture
def ctx() -> FunctionContext:
    c = cast(FunctionContext, FakeContext())
    migrate.handle(c, _event({}))
    return c


def test_migrate_applies_each_migration_once() -> None:
    c = cast(FunctionContext, FakeContext())
    assert migrate.handle(c, _event({})) == {"applied": len(MIGRATIONS)}
    assert migrate.handle(c, _event({})) == {"applied": 0}


def test_a_failed_migration_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    c = cast(FunctionContext, FakeContext())
    monkeypatch.setitem(MIGRATIONS, "0003_broken", ["ALTER TABLE missing ADD COLUMN x"])
    with pytest.raises(Exception, match="missing"):
        migrate.handle(c, _event({}))
    assert c.sql.query("app", "SELECT version FROM _migrations") == []


def test_orders_are_placed_listed_and_changed(ctx: FunctionContext) -> None:
    orders.handle(ctx, _event({"op": "place", "email": "ada@example.org", "item": "tea", "quantity": 2}))
    orders.handle(ctx, _event({"op": "place", "email": "ada@example.org", "item": "cups", "quantity": 6}))
    orders.handle(ctx, _event({"op": "place", "email": "bob@example.org", "item": "tea", "quantity": 1}))
    assert orders.handle(ctx, _event({"op": "ship", "id": 1})) == {"changed": 1}
    assert orders.handle(ctx, _event({"op": "note", "id": 2, "note": "gift wrap"})) == {"changed": 1}

    listed = orders.handle(ctx, _event({"op": "list", "email": "ada@example.org"}))["orders"]
    assert [(o["item"], o["quantity"], o["status"], o["note"]) for o in listed] == [
        ("tea", 2, "shipped", None),
        ("cups", 6, "placed", "gift wrap"),
    ]
    assert len(ctx.sql.query("app", "SELECT id FROM customers")) == 2, "a customer is added once"


def test_audit_records_a_change(ctx: FunctionContext) -> None:
    change = {
        "op": "Update",
        "table": "orders",
        "rowId": 7,
        "before": {"status": "placed"},
        "after": {"status": "shipped"},
    }
    assert audit.handle(ctx, _event(change)) == {"recorded": 1}
    assert orders.handle(ctx, _event({"op": "history", "id": 7})) == {
        "history": [{"change": "Update", "status": "shipped"}]
    }


def test_the_rendered_migrations_are_current() -> None:
    import render_migrations

    assert render_migrations.render() == render_migrations.OUT.read_text()
