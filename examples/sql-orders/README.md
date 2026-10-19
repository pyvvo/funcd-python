# sql-orders — a SQL database with SQLModel, Alembic and a change feed (funcd FEAT-0011)

Three Python functions over one SQL database, `orders-db`, served by its own rqlite engine:

| Function | What |
|---|---|
| `orders-migrate` | applies the Alembic migrations the database lacks |
| `orders` | places, lists, ships, cancels and annotates orders, and returns an order's audit trail with SQLModel; the owner of `orders-db` |
| `audit` | records each change of table `orders` that the change feed delivers, in table `order_events` |

The functions hold no credential and never reach the engine: `context.sql` calls funcd over the sandbox's socket,
and funcd checks the binding and the roles before it forwards the statements (funcd ADR-0233).

## Layout

Each function is `src/<name>/main.py`, its entry in `pyproject.toml`'s `[tool.funcd-bundle.functions]`, with
`<name>.contract.yaml` at the root (funcd ADR-0247). The code the three share is the `orders-core` workspace package
in [`packages/orders-core`](packages/orders-core), which `funcd-bundle` vendors into each bundle as a wheel.

## Models and migrations

[`orders_core/models.py`](packages/orders-core/src/orders_core/models.py) holds the SQLModel tables. Their schema changes are Alembic revisions in
[`alembic/versions`](alembic/versions): `0001_init` creates the tables and `0002_order_note` adds a column. A revision
only adds to the schema, so the code one version back still runs on it.

Alembic never connects to the database. [`scripts/render_migrations.py`](scripts/render_migrations.py) renders each
revision offline to the SQLite statements it runs, without Alembic's own transaction and version table, into
[`orders_core/migrations_sql.py`](packages/orders-core/src/orders_core/migrations_sql.py), which the `orders-migrate` function ships:

```bash
uv run alembic revision -m "<what changed>"       # write the upgrade in the new alembic/versions file
uv run python scripts/render_migrations.py        # regenerate orders_core/migrations_sql.py; a test fails while it is stale
```

`orders-migrate` applies the revisions that table `_migrations` lacks, with their `_migrations` rows, in one `context.sql`
call. rqlite runs the call as one transaction, so a failed migration records nothing. Call it after each deploy, or
make it an App's `preApply` hook (funcd ADR-0236).

## Queries

[`orders_core/sqlrun.py`](packages/orders-core/src/orders_core/sqlrun.py) compiles a SQLModel statement with the SQLite dialect into SQL text and parameters
for `context.sql`. A call with several statements applies all of them or none: placing an order adds its customer on
the first order and the order itself together.

```python
stmt = select(Order, Customer.email).join(Customer, col(Customer.id) == col(Order.customer_id))
rows(context, stmt)
```

## The change feed

[`changes.yaml`](changes.yaml) declares EventSource `orders-changes` on table `orders` and a Sensor that calls
`audit` with each insert, update and delete. The engine posts each committed change to funcd, which publishes it as
event `order-changed` with the row before and after (funcd ADR-0235). A change delivered twice starts `audit` once.

The Sensor calls `audit` once per change, and the calls run independently: the first one may wake the function
from zero and finish after the next. So `audit` stores each change's commit `index`, and `history` sorts by it, not
by arrival. The changes of one commit share an index.

## Run it locally

`funcdctl dev` runs the three functions from source with `orders-db` on a local rqlite engine (funcd ADR-0237); a
funcdctl built with the engine (`just build-sql-engine` in funcd) is needed. It runs functions only, so call `audit`
by hand there:

```bash
uv sync
funcdctl dev .
curl -XPOST localhost:<gateway>/function/orders-migrate -d '{"data":{}}'
curl -XPOST localhost:<gateway>/function/orders -d '{"data":{"op":"place","email":"ada@example.org","item":"tea","quantity":2}}'
curl -XPOST localhost:<gateway>/function/orders -d '{"data":{"op":"list"}}'
```

## Deploy it

```bash
uv run funcd-bundle                    # dist/<function>/ with SQLModel vendored for the runtime
funcdctl push dist/orders <registry>:orders
funcdctl push dist/orders-migrate <registry>:orders-migrate
funcdctl push dist/audit <registry>:audit
funcdctl apply -f orders-db.yaml
funcdctl apply -f orders.yaml
funcdctl apply -f orders-migrate.yaml
funcdctl apply -f audit.yaml
funcdctl apply -f grant.yaml
funcdctl apply -f changes.yaml
```

| File | What |
|---|---|
| `orders-db.yaml` | SQLDatabase `orders-db`, owned by `orders` |
| `orders.yaml`, `orders-migrate.yaml`, `audit.yaml` | the Functions, each binding `orders-db` as alias `app` |
| `grant.yaml` | `SQL Data Writer` on `orders-db` for `orders-migrate` and `audit` |
| `changes.yaml` | the EventSource on table `orders` and the Sensor that calls `audit` |
| `funcdconfig.yaml` | a daemon config with file storage; the engine posts its changes to the bridge address funcd derives (funcd ADR-0238) |

A workload BackupSchedule that lists `orders-db` in `scope.databases` copies the database, sealed to its recipients, and a
Restore loads a copy into an empty database (funcd ADR-0234).

## Tests

`uv run pytest` runs the migrations and the three functions against an in-memory SQLite behind a fake
`context.sql` that runs a batch in one transaction, as rqlite does, and checks that `orders_core/migrations_sql.py` is current.
