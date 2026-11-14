# lake-quack

The Lake `lake` and the Function `lake-reader`, which runs the SQL of each call on the Lake and returns the rows. The
Lake keeps its tables as Parquet files under the prefix `gold` of the Bucket `lakehouse`, and its catalog in funcd's
catalog store, outside the Bucket.

| File | What it holds |
| --- | --- |
| `lake.yaml` | The Lake `lake`, with the Bucket `lakehouse` that it declares and owns |
| `configmap.yaml` | `lake-engine-config`, the engine's DuckDB settings |
| `secret.yaml` | `lake-quack-token`, the token that the engine accepts |
| `consumer.yaml` | The Function `lake-reader`, which declares and binds the Lake |
| `src/main.py` | The handler: it reads `FUNCD_LAKE_LAKE_URL` and `FUNCD_LAKE_LAKE_TOKEN` and runs the SQL with `quack_query` |
| `lake-reader.contract.yaml` | The Function's input, `{sql: string, lake?: string}`, and its output |
| `funcdconfig.yaml` | The server configuration of funcd's `duckdb` lane |

## Run it locally

The handler imports the `duckdb` package. From the repository's root folder, install it in a virtual environment, at
the version of the engine that `funcdctl dev` runs, and start the example with that environment first on the `PATH`:

```bash
python3 -m venv .venv
.venv/bin/pip install duckdb==2.0.0
PATH="$PWD/.venv/bin:$PATH" funcdctl dev examples/lake-quack --gport 8080
```

In another terminal, create a table in the Lake, then read it back:

```bash
curl -s -X POST -H 'content-type: application/json' -d '{"sql":"CREATE TABLE lakehouse.readings AS SELECT * FROM (VALUES (1, 19), (2, 21)) AS t(sensor, celsius)"}' http://127.0.0.1:8080/function/lake-reader
curl -s -X POST -H 'content-type: application/json' -d '{"sql":"SELECT sensor, celsius FROM lakehouse.readings ORDER BY sensor"}' http://127.0.0.1:8080/function/lake-reader
```

The first call answers `{"rows":[[2]]}`, the number of rows written, and the second `{"rows":[[1,19],[2,21]]}`.

## Deploy it

On a platform, the Function's bundle carries the `duckdb` package and the `quack` and `httpfs` extensions. Building it
needs Docker: the build installs the locked `duckdb` in `python:3.14-slim-bookworm` for the target platform, then runs
`scripts/install_extensions.py` there to download the extensions built for it, and `--platform linux/amd64` builds for
the other architecture under emulation. Push the bundle, set the `image` of `consumer.yaml` to the same reference, and
apply the files in any order, one file per apply:

```bash
uv run funcd-bundle                  # writes dist/lake-reader/
funcdctl push dist/lake-reader <ref>
for f in configmap.yaml secret.yaml lake.yaml consumer.yaml; do funcdctl apply -f "$f"; done
```

The Lake creates its own Bucket and waits for the ConfigMap and the Secret that it declares, and the Function waits in
`Pending` until the Lake is `Ready`.
