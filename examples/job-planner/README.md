# job-planner — a Function that starts a Job's run (Python)

A **Python** Function (funcd ADR-0349), the sibling of funcd-typescript's `examples/job-planner`. The Function
`pyplanner` binds the Job `pyreport` of the [job-report](../job-report) example under the alias `report`. For each
event, its handler starts a run of `pyreport` for the event's month, named `pyreport-<month>`, and reads the run back:

```
{"month":"2026-10"} ──▶ handle(context, event) ──▶ context.job.start ──▶ {"run":"pyreport-2026-10","phase":"Pending"}
```

```
src/main.py               # the handler, with its input and output types PlanInput and PlanOutput
tests/test_handler.py     # the author's unit tests, with no platform
planner.yaml              # the Function resource, with its bindings.job
pyplanner.contract.yaml   # the artifact's runtime and I/O contract (funcd ADR-0247): month alone, required
pyproject.toml            # uv project; depends on funcd-shim for the types
```

`context.job.start` returns the run's name at once, without waiting for the run. The name comes from the month, so a
start sent again for one month, with its input, returns the run that the first start made: an event that funcd
delivers twice starts one run. `context.job.get` reads a run of the bound Job: its phase, its reason, its times and,
once it Succeeded, its result.

## Develop

```bash
uv sync            # create the venv with funcd-shim and the dev tools
uv run mypy        # strict typecheck against funcd_shim's FunctionContext
uv run ruff check  # lint
uv run pytest      # the handler's unit tests
```

## Run it

Apply the Job `pyreport` of the job-report example first. It needs no third-party package, so it is pushed as one
file: `src/main.py`, staged as `pyplanner.py` beside its contract file. Point `image` in `planner.yaml` at the
reference that the push prints, apply the Function and call it on funcd's data plane:

```bash
mkdir -p dist && cp src/main.py dist/pyplanner.py && cp pyplanner.contract.yaml dist/
funcdctl push dist/pyplanner.py "oci-layout://$PWD/registry:pyplanner"
funcdctl apply -f planner.yaml
curl -s -X POST -H 'content-type: application/json' -d '{"month":"2026-10"}' http://127.0.0.1:8081/function/pyplanner
# → {"run":"pyreport-2026-10","phase":"Pending"}
funcdctl job describe pyreport-2026-10
```
