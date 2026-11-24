# job-report — a Job that reports on one month (Python)

A **Python** Job handler (funcd ADR-0347), the sibling of funcd-typescript's `examples/job-report`. Each run of the
Job `pyreport` takes a month as `YYYY-MM`, logs it with the name of its run, works for three seconds, so that a
cancel can stop the run while it runs, and returns one row for each day of the month:

```
{"month":"2026-10"} ──▶ handle(context, event) ──▶ {"month":"2026-10","rows":31}
```

```
src/main.py              # the handler, with its input and output types JobInput and JobOutput
tests/test_handler.py    # the author's unit tests, with no platform
report.yaml              # the Job resource (runtime: python314, handler: handle, timeout, concurrency)
pyreport.contract.yaml   # the artifact's runtime and I/O contract (funcd ADR-0247): month alone, required
pyproject.toml           # uv project; depends on funcd-shim for the types
```

`context.run` holds the run's facts: its `name`, its `job`, the `revision` that it pinned, its `phase` and
`started_by`, the subject of the credential that created the run.

## Develop

```bash
uv sync            # create the venv with funcd-shim and the dev tools
uv run mypy        # strict typecheck against funcd_shim's JobContext
uv run ruff check  # lint
uv run pytest      # the handler's unit tests
```

## Run it

It needs no third-party package, so it is pushed as one file: `src/main.py`, staged as `pyreport.py` beside its
contract file. Point `image` in `report.yaml` at the reference that the push prints, apply the Job to a running
funcd, then start a run and read it:

```bash
mkdir -p dist && cp src/main.py dist/pyreport.py && cp pyreport.contract.yaml dist/
funcdctl push dist/pyreport.py "oci-layout://$PWD/registry:pyreport"
funcdctl apply -f report.yaml
funcdctl job run pyreport pyreport-1 --input '{"month":"2026-10"}'
funcdctl job describe pyreport-1   # → result: {"month":"2026-10","rows":31}
funcdctl job logs pyreport-1       # → building the report of 2026-10 in run pyreport-1
```

funcd's `job` lane runs it on containerd, and the Job page of funcd's guide walks through it.
