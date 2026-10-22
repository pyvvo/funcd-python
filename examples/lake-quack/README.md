# lake-quack — the F48 DuckLake/DuckDB/Quack add-on provider (ADR-0086 + ADR-0087)

Deploy a governed SQL **lake + query** engine *on* funcd: a `Lake` that the
**add-on provider runtime** (ADR-0087) brings up as a curated `duckdb` engine — DuckDB + DuckLake +
Quack, out-of-process (no cgo in the daemon), reading/writing Parquet through the F47 S3 surface and
checkpointing its SQLite catalog to blob.

## Run it locally (`funcdctl dev`)

`funcdctl dev` runs the consumer function from its resource and contract files (funcd ADR-0247) — printing a colored
services banner + live logs (it builds the `-tags dev` funcdctl for you). The **first** run fetches
the embedded DuckDB+Quack lake engine for your platform (once — `just dev-example` handles it),
and the example's `lakes` binding (alias `lake`) is wired automatically:

```bash
just dev-example python/lake-quack      # gateway :3005 · S3 :3006 — override: just dev-example python/lake-quack 4000 4001
```

Invoke the gateway the banner prints (default `http://127.0.0.1:3005`). Dev runs the Function its
resource file names (`lake-reader`), from `src/main.py` with `lake-reader.contract.yaml`; POST the input **directly**
— the gateway builds the CloudEvent (ADR-0134), matching the contract file's `input`
(`{sql: string, lake?: string}`); a full `{"data": <input>}` envelope still works too:

```bash
curl -sS -XPOST http://127.0.0.1:3005/function/lake-quack \
  -H 'Content-Type: application/json' -d '{"sql":"SELECT 42 AS answer"}'
# → {"rows":[[42]]}
```

## What you apply (the user-facing flow)

```bash
# Apply in any order: the Lake creates its own Bucket (funcd ADR-0251) and waits for the ConfigMap
# and the Secret it declares. funcdctl apply -f takes one file (or - for stdin), so apply each file:
for f in configmap.yaml secret.yaml lake.yaml consumer.yaml; do funcdctl apply -f "$f"; done
```

- **`configmap.yaml`** — non-secret engine tuning (`DUCKDB_*`), consumed via `spec.bindings.config` (the
  ADR-0057 convention, extended to `Lake` by ADR-0087).
- **`secret.yaml`** — the Quack auth token, consumed via `spec.bindings.secret`. A Quack client **always**
  needs a token (a token-less client is refused), so the engine serves with `QUACK_TOKEN` and every
  consumer presents the same; the ingress gateway is the authoritative *outer* gate.
- **`lake.yaml`** — the `lake` Lake: at its root the `lakehouse` Bucket it owns (its
  `gold` prefix holds the Parquet and the `_ducklake/catalog.db` SQLite catalog), the ConfigMap by `ref` and the
  Secret's declaration (funcd ADR-0251); then the `gold` blob binding, the lake ref, resources, `config` and
  `secret`.

## Consuming the lake — a funcd Function

The consumer is a **funcd Function** (`src/main.py` + `consumer.yaml`) — the funcd-native shape, not a
CLI. Invoke it (`POST /function/lake-reader {"data":{"sql":"SELECT …"}}`) and it runs the SQL on the
`lake` Lake over Quack and returns the rows. A Quack client *is* a local DuckDB with the
`quack` extension (there is no separate JS/Python Quack library — Quack is DuckDB-to-DuckDB, Protobuf
over HTTP), so the handler `import duckdb` and calls `quack_query(uri, sql, token, …)`.

**DuckDB travels in the artifact — no `python-duckdb` runtime (ADR-0089).** The consumer runs on the
**stock curated `python314`** runtime; its DuckDB wheel + the `quack`/`httpfs` extensions are vendored
into a deployment-package **bundle** (a directory) that `funcdctl push` turns into one tar+gzip OCI layer.
Build it with `funcd-bundle` (funcd ADR-0144) and push it:

```bash
uv run funcd-bundle                                      # → dist/lake-reader/  (main.py + vendored duckdb + duckdb-ext/ + lake-reader.contract.yaml)
funcdctl push dist/lake-reader <ref>
```

The build is hermetic (`[tool.funcd-bundle]` in `pyproject.toml`): it installs the locked `duckdb` inside
`python:3.14-slim-bookworm` for the target platform, then runs `scripts/install_extensions.py` there to
download the `quack` and `httpfs` extensions built for that platform. `--platform linux/amd64` builds for
the other architecture under emulation.

funcd untars the bundle into the artifact dir and sets `PYTHONPATH` + `FUNCD_BUNDLE_DIR`, so `import duckdb`
and the offline `duckdb-ext/` extensions resolve with **zero** runtime changes. `funcdctl push` takes the
runtime and the `{input, output}` contract from the bundle's `lake-reader.contract.yaml`, gates it and promotes it to the OCI
contract layer (`funcdctl inspect`).

**One remaining deploy prerequisite** (a follow-up — Project #4 *"Provider consumption binding"*):

- **The `spec.bindings.lake` consumer binding** — it injects `FUNCD_LAKE_LAKE_URL`/`_TOKEN` and opens the
  egress grant (function → lake). Until it lands, bind the token via `spec.bindings.secret` + the URL via a
  ConfigMap.

So the live `just lima-example duckdb` lane asserts the **provider** side end-to-end (deploy → Ready →
serving Quack → S3-authorized); the function-consumer round-trip lands with that one follow-up. The
handler's logic is unit-tested now (`uv run pytest`).

Then the **Lake reconciler** (reworked by ADR-0087) derives the per-fn S3 keypair over the
provider identity, resolves `config`/`secrets` into the engine env, assembles a `provider.ProviderSpec`,
and the **provider-runtime** `Create`/`Start`s the `duckdb` engine container (no backing Function, no
Function shape gate), probes its HTTP readiness (`GET /`→`200`), and publishes `status` — all automatic.

Building the consumer bundle needs Docker (the hermetic install and the import check).

## Live status — working end-to-end (ADR-0086 + ADR-0087 + ADR-0088)

Verified on real containerd (`just lima-example duckdb`): the Lake deploys as an add-on
provider (no backing Function), the provider-runtime brings up the `duckdb` engine, it reaches **Ready**
on its HTTP readiness probe (`curl http://<status.address>/` → `200`), and its keypair reads/writes S3
through the F47 PEP (`HEAD` on the lake → `404` fresh, **not** `403`).

The keystone was **ADR-0088 (provider F47/Cedar identity)**: the Cedar EntityProvider now sources a
provider principal's `blobBindings` from the `Lake.spec.bindings.blob` (Function-first) and the
prefix-owner admission accepts a `Lake` owner — so `lake` owning the `gold` prefix of its Bucket is admitted
and the engine's S3 access is authorized, all under the same binding-as-grant as a function (the S3
policy is unchanged). Two earlier provider-runtime fixes also fell out of building this lane: the
readiness probe addresses the fixed `spec.Port` (not the portfile-resolved `Instance.Port`, which is 0
for an image-entrypoint engine), and the reconciler **requeues** while the engine boots so it
auto-progresses to Ready.

## The e2e lane

funcd's `scripts/lanes.yaml` `duckdb` lane + `e2e/duckdb.venom.yml` + `just lima-example duckdb` are the live lane on
real containerd. They assert the **provider** side end-to-end: the Lake deploys (no backing
Function) → the provider-runtime brings up the `duckdb` engine → it reaches Ready → it serves Quack
(`GET /`→`200`) and its S3 access is authorized (ADR-0088 — no `403`). The **consumer Function**
(`consumer.yaml` + `src/main.py`) round-trip lands with the two follow-ups above (the duckdb-capable
function runtime + the `spec.bindings.lake` binding).
