# funcd hello-world (Python)

A minimal funcd function authored in **Python**, typed against the `funcd_shim` contract
(ADR-0049) with a typed I/O contract (ADR-0058). The Python sibling of
[`examples/hello-world` in pyvvo/funcd-typescript](https://github.com/pyvvo/funcd-typescript/tree/main/examples/hello-world) — same `handle(context, event)` contract, same
`FuncInput`/`FuncOutput` model, checked identically by funcd (funcd ADR-0303).

```
src/main.py           # the function: handle(context, event) + its FuncInput/FuncOutput types
tests/test_handler.py # the author's unit tests (no platform needed)
handler.yaml          # the Function resource (runtime: python314, handler: handle)
hello-world.contract.yaml # the artifact's runtime and I/O contract (funcd ADR-0247)
pyproject.toml        # uv project; depends on funcd-shim for the typed contract
```

## The I/O contract

The two `TypedDict`s **`FuncInput`** and **`FuncOutput`** *are* the contract. A `TypedDict` is the
runtime-honest choice: at runtime `event["data"]` is a *plain dict* (exactly what the shim hands
you), while the type still gives `CloudEvent[FuncInput]` typed-key autocomplete under `mypy`.

```python
from typing import TypedDict
from funcd_shim import CloudEvent, FunctionContext

class FuncInput(TypedDict):
    name: str

class FuncOutput(TypedDict):
    greeting: str

def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    assert "data" in event             # parallel of TS `event.data!` — narrows the optional key
    data = event["data"]               # already validated → present & well-shaped, typed FuncInput
    return {"greeting": f"Hello, {data['name']}."}
```

The build generates the schema from the contract type, so you
declare a *type*, never a validator, and `from __future__ import annotations` is fine either way
(the build resolves the field types). The runtime artifact carries the future-import regardless.

### How the contract is enforced (ADR-0058 / ADR-0303)

You supply a *type*, never a validator. At `funcdctl push` the build:

1. generates a closed **JSON Schema** from `FuncInput` / `FuncOutput` (pydantic
   `TypeAdapter().json_schema()`, **build-time only** — closing records the profile requires);
2. gates it against the supported **profile** (closed records, scalars, enums, arrays,
   string-keyed maps, discriminated unions, `Json`; no open records, no recursion).

At call time funcd checks each call against the contract around your handler (funcd ADR-0303):

| Situation | Result |
|---|---|
| `event["data"]` doesn't match `FuncInput` | **422** — `handle` is never called |
| return value doesn't match `FuncOutput` | **500** — the bug is server-side |
| handler returns `None` | **204** |
| no `FuncInput`/`FuncOutput` declared | unvalidated (the pre-contract V1 behavior) |

### Other shapes in the profile

```python
type FuncInput = Json                  # accept any JSON value (empty schema {})

class Accepted(TypedDict):             # discriminated union — `kind` is the tag
    kind: Literal["accepted"]
    id: str
class Rejected(TypedDict):
    kind: Literal["rejected"]
    reason: str
FuncOutput = Accepted | Rejected       # generated as a tagged oneOf + discriminator

def handle(context, event) -> None:    # FuncOutput = None  → returns nothing → 204
    ...
```

A discriminated union (each branch a closed record sharing a required literal tag) builds to the
profile's tagged `oneOf` + `discriminator` — the build inlines pydantic's `$ref`s and converts its
`anyOf` to the form the gate accepts. A pydantic `BaseModel` also works as the contract type, but it
*type-lies* at runtime — the handler still receives a plain dict, never a model instance — so a
`TypedDict` is preferred for honesty. The top-level contract must be a type pydantic can read
(a `TypedDict`/`BaseModel`, a union of those, or `Json`); recursive types are out of profile.

## Develop

Managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync            # create the venv, install funcd-shim + dev tools
uv run mypy        # strict typecheck against the funcd_shim Handler contract
uv run ruff check  # lint
uv run pytest      # run the unit tests
```

**Editor typing.** For `event: CloudEvent[FuncInput]` / `event["data"]: FuncInput` to resolve
in-editor, Pylance must find the shim source. Pylance resolves imports against the selected
interpreter plus `python.analysis.extraPaths`; it ignores `pyrightconfig.json`'s `venv` key, and
it only reads a `pyrightconfig.json` at the **workspace root**. So:

- **Opening this folder as the workspace** (`code examples/hello-world`): the local
  [`pyrightconfig.json`](pyrightconfig.json) path-maps `funcd_shim` to the in-repo shim source
  (`extraPaths: ["../../shim/src"]`) — the parallel of the TS example's `tsconfig.json`
  map — and typing just works (no interpreter switch, no `uv sync` needed).
- **Opening the whole repo**: the nested config is ignored, so add the shim source to your
  *workspace* settings (`.vscode/settings.json`, which this repo gitignores):
  `"python.analysis.extraPaths": ["shim/src"]`. Then it resolves under any interpreter.

Without either, an interpreter that lacks `funcd_shim` shows `event: Any` (an unresolved import
collapses the generic `TypedDict` subscript to `Any`).

## Run it locally (`funcdctl dev`)

`funcdctl dev` runs the function from its resource and contract files (funcd ADR-0247) — printing a colored services
banner + live logs (it builds the `-tags dev` funcdctl for you):

```bash
just dev-example python/hello-world      # gateway :3005 · S3 :3006 — override: just dev-example python/hello-world 4000 4001
```

Invoke the gateway the banner prints (default `http://127.0.0.1:3005`). Dev runs the Function its
resource file names (`hello-world`), from `src/main.py` with `hello-world.contract.yaml`; POST the input **directly** —
the gateway builds the CloudEvent (ADR-0134). It matches the contract file's `input`
(`{name: string}`); a full `{"data": <input>}` envelope still works too:

```bash
curl -sS -XPOST http://127.0.0.1:3005/function/hello-world \
  -H 'Content-Type: application/json' -d '{"name":"funcd"}'
# → {"greeting":"Hello, funcd."}
```

## Deploy

It needs no third-party package, so it is pushed as one file: `src/main.py`, staged as `hello-world.py` beside
its contract file (funcd ADR-0247). Push it to the image that [`handler.yaml`](handler.yaml) names, then apply it (`runtime: python314`, `handler: handle`):

```bash
curl -fsSLO https://raw.githubusercontent.com/pyvvo/funcd/main/examples/funcdconfig.yaml
funcd --config funcdconfig.yaml &         # start the daemon (zero-infra dev config, ADR-0061)
mkdir -p dist && cp src/main.py dist/hello-world.py && cp hello-world.contract.yaml dist/   # a single file pushes with the contract file of its name
funcdctl push dist/hello-world.py oci-layout:///tmp/funcd-demo/layout:hello-world
funcdctl apply -f handler.yaml
```

[`examples/funcdconfig.yaml`](https://github.com/pyvvo/funcd/blob/main/examples/funcdconfig.yaml) in
pyvvo/funcd is the shared daemon config (in-memory substrate + process runtime + localhost
addresses); it's optional — `funcd` runs with all defaults if omitted.

The platform's curated Python image runs the shim, which loads this module, invokes `handle` and
returns the dict as the 200 JSON body. funcd checks each event's `data` against the pushed contract
first and the result after (funcd ADR-0303), so a mismatched event is rejected **422** before
`handle` runs.
