# sql-readings — a Workflow whose steps share a SQL database (funcd FEAT-0011)

Workflow `readings` has two Python steps over SQLDatabase `readings-db`, written with SQLAlchemy Core, no ORM:

| Step | What |
|---|---|
| `ingest` | stores a batch of one sensor's values after the ones it holds, creating the table and its index on first use, in one call |
| `summarize` | reads them back: count, mean, low and high through a CTE, and the latest values with a moving average and a rank through window functions |

A step binds a database like a Function does (`function.sql`, funcd ADR-0233). The engine materializes each image
step as Function `readings-<step>`, so the database's owner is `readings-ingest`, and `summarize` reads without a
grant. The statements run in the database's rqlite engine; only the results return to the step.

```python
mine = select(readings.c.seq, readings.c.value).where(readings.c.sensor == sensor).cte("mine")
func.avg(mine.c.value).over(order_by=mine.c.seq, rows=(-2, 0))   # the average of a value and the two before it
```

[`readings_core/tables.py`](packages/readings-core/src/readings_core/tables.py), in the `readings-core` workspace package both steps share (each step is
`src/<name>/main.py` with `<name>.contract.yaml`, funcd ADR-0247), compiles each statement with the SQLite dialect into SQL text and parameters for
`context.sql`; several statements in one call apply all or none.

## Run it locally

`funcdctl dev .` runs both steps from source with `readings-db` on a local rqlite engine (funcd
ADR-0237); a funcdctl built with the engine (`just build-sql-engine` in funcd) is needed. Start a run through its
control plane; dev puts its objects in resource group `dev`:

```bash
uv sync
funcdctl dev . --cport 3107
sed 's/resourceGroup: rg1/resourceGroup: dev/' run.yaml | FUNCD_SERVER=http://127.0.0.1:3107 FUNCD_TOKEN=funcd-dev-token funcdctl apply -f -
```

## Deploy it

```bash
uv run funcd-bundle
funcdctl push dist/ingest <registry>:ingest
funcdctl push dist/summarize <registry>:summarize
funcdctl apply -f readings-db.yaml
funcdctl apply -f workflow.yaml
funcdctl apply -f run.yaml
```

## Tests

`uv run pytest` runs both steps against an in-memory SQLite behind a fake `context.sql`.
