"""Tests for funcd_build — the build-time contract build (ADR-0058/0060).

Proves that a pydantic model becomes a closed JSON Schema, and that the runtime artifact the worker
loads carries NO pydantic (so it runs in a subinterpreter) and no validator: funcd checks each call
against the schema (funcd ADR-0303)."""

from __future__ import annotations

import ast
import sys
from typing import Any

import pytest

from funcd_shim.build import build

if sys.version_info >= (3, 14):
    from concurrent import interpreters  # type: ignore[attr-defined]

_SRC = (
    "from pydantic import BaseModel\n"
    "from funcd_shim import CloudEvent, FunctionContext\n"
    "\n"
    "class FuncInput(BaseModel):\n"
    "    order_id: str\n"
    "    qty: int\n"
    "\n"
    "class FuncOutput(BaseModel):\n"
    "    accepted: bool\n"
    "\n"
    "def handle(ctx, event):\n"
    "    return {'accepted': event['data']['qty'] > 0}\n"
)


def _exec(source: str) -> dict[str, Any]:
    ns: dict[str, Any] = {}
    exec(compile(source, "<rt>", "exec"), ns)  # noqa: S102 - exercising the generated artifact
    return ns


def test_build_emits_schemas_from_the_models() -> None:
    r = build(_SRC)
    assert r.input_schema is not None and r.input_schema["properties"]["qty"]["type"] == "integer"
    assert r.output_schema is not None and r.output_schema["properties"]["accepted"]["type"] == "boolean"
    # records are CLOSED (funcd profile) — pydantic emits them open, funcd_build closes them, or the
    # Go gate (contract.Check) would reject the contract as an open record.
    assert r.input_schema["additionalProperties"] is False
    assert r.output_schema["additionalProperties"] is False


# a TypedDict contract — the type-honest `CloudEvent[FuncInput]` DX. pydantic reads it via
# TypeAdapter at build, the runtime value is a plain dict (so event["data"]["qty"] is typed AND
# correct at runtime). No pydantic at runtime.
_TYPEDDICT_SRC = (
    "from typing import TypedDict\n"
    "from funcd_shim import CloudEvent, FunctionContext\n"
    "\n"
    "class FuncInput(TypedDict):\n"
    "    order_id: str\n"
    "    qty: int\n"
    "\n"
    "class FuncOutput(TypedDict):\n"
    "    echoed: int\n"
    "\n"
    "def handle(ctx: FunctionContext, event: CloudEvent[FuncInput]):\n"
    "    return {'echoed': event['data']['qty']}\n"
)


def test_typeddict_contract_closed_schema() -> None:
    r = build(_TYPEDDICT_SRC)
    assert r.input_schema is not None
    assert r.input_schema["properties"]["qty"]["type"] == "integer"
    assert r.input_schema["additionalProperties"] is False, "a TypedDict record is closed too"


def test_built_artifact_runs_in_both_compute_modes() -> None:
    """The SAME built artifact loads and handles SOLO (non-pooled) and inside a SUBINTERPRETER (the
    ADR-0050 pool), which pydantic-core cannot do — so the build strips pydantic from it."""
    rt = build(_TYPEDDICT_SRC).runtime_source

    # expressed as asserts so a failure RAISES — works the same in-process and across an interpreter
    # boundary (subinterpreters don't share objects, but a raised exception propagates to the parent).
    checks = rt + "\nassert handle(None, {'data': {'order_id': 'a', 'qty': 7}}) == {'echoed': 7}\n"

    # NON-POOLED (solo): run in this interpreter.
    exec(compile(checks, "<solo>", "exec"), {})  # noqa: S102 - exercising the generated artifact

    # POOLED (subinterpreter): run the SAME artifact in a fresh subinterpreter (ADR-0050, 3.14+).
    if sys.version_info >= (3, 14):
        interp = interpreters.create()
        try:
            interp.exec(checks)  # raises into the parent if any assert fails inside the subinterpreter
        finally:
            interp.close()


def test_runtime_artifact_strips_pydantic_and_bakes_no_validator() -> None:
    rt = build(_SRC).runtime_source
    # pydantic + the contract classes are gone (so a subinterpreter can load it), and funcd checks the
    # calls, so the build bakes no validator (funcd ADR-0303).
    assert "from pydantic" not in rt
    assert "class FuncInput" not in rt
    assert "class FuncOutput" not in rt
    assert "__funcd_validate" not in rt
    assert "def handle" in rt
    # the author's funcd_shim import (CloudEvent/FunctionContext) is kept; it pulls no pydantic.
    assert "from funcd_shim import" in rt
    # __future__ import is the very first statement (annotations are stringized → safe).
    tree = ast.parse(rt)
    assert isinstance(tree.body[0], ast.ImportFrom) and tree.body[0].module == "__future__"


def test_runtime_artifact_handles_without_pydantic() -> None:
    handle = _exec(build(_SRC).runtime_source)["handle"]
    # the handler runs on a plain dict (event['data']), never a pydantic instance.
    assert handle(None, {"data": {"order_id": "o", "qty": 5}}) == {"accepted": True}


# scenario: void-output-schema-explicit (ADR-0090) — `FuncOutput = None` emits an EXPLICIT
# {"type":"null"} output schema (was: omitted).
def test_scenario_void_output_schema_explicit() -> None:
    src = (
        "from pydantic import BaseModel\n"
        "class FuncInput(BaseModel):\n"
        "    x: int\n"
        "FuncOutput = None\n"  # explicit void contract
        "def handle(ctx, event):\n"
        "    return None\n"
    )
    assert build(src).output_schema == {"type": "null"}, (
        "a void output is the explicit {'type':'null'} schema"
    )


# scenario: void-input-schema-explicit (ADR-0090) — the symmetric void-input marker `FuncInput = None`
# emits an EXPLICIT {"type":"null"} input schema.
def test_scenario_void_input_schema_explicit() -> None:
    src = (
        "from pydantic import BaseModel\n"
        "FuncInput = None\n"  # explicit void INPUT (new symmetric marker)
        "class FuncOutput(BaseModel):\n"
        "    ok: bool\n"
        "def handle(ctx, event):\n"
        "    return {'ok': True}\n"
    )
    assert build(src).input_schema == {"type": "null"}, "a void input is the explicit {'type':'null'} schema"


# scenario: undeclared-io-is-error (ADR-0090) — the "unchecked" path is gone: a handler that fails to
# declare FuncInput or FuncOutput is a BUILD error (the author must declare, `None` for void).
def test_scenario_undeclared_io_is_error() -> None:
    with pytest.raises(ValueError, match="FuncInput"):
        build("def handle(ctx, event):\n    return {'ok': True}\n")
    with pytest.raises(ValueError, match="FuncOutput"):
        build("FuncInput = None\ndef handle(ctx, event):\n    return None\n")


# a top-level DISCRIMINATED UNION contract (FuncOutput = A | B) — pydantic emits anyOf + $ref; the
# build inlines the refs and rewrites it to the profile's tagged `oneOf` + `discriminator`.
_UNION_SRC = (
    "from typing import TypedDict, Literal\n"
    "from funcd_shim import CloudEvent, FunctionContext\n"
    "\n"
    "class Accepted(TypedDict):\n"
    "    kind: Literal['accepted']\n"
    "    id: str\n"
    "class Rejected(TypedDict):\n"
    "    kind: Literal['rejected']\n"
    "    reason: str\n"
    "FuncInput = None\n"  # void input (ADR-0090); focus is the discriminated OUTPUT union
    "FuncOutput = Accepted | Rejected\n"
    "\n"
    "def handle(ctx, event):\n"
    "    return {'kind': 'accepted', 'id': 'x'}\n"
)


def test_build_discriminated_union_output() -> None:
    out = build(_UNION_SRC).output_schema
    assert out is not None
    assert "anyOf" not in out, "a bare anyOf would be rejected by the gate"
    assert "$ref" not in repr(out), "refs must be inlined (the profile forbids $ref)"
    assert isinstance(out.get("oneOf"), list) and len(out["oneOf"]) == 2
    assert out["discriminator"]["propertyName"] == "kind"
    for branch in out["oneOf"]:
        assert branch["additionalProperties"] is False  # branches are closed records


# the contract module uses `from __future__ import annotations` (the project's default style) + a
# non-builtin field type (Literal) — the build must still resolve the field types to a schema.
_FUTURE_ANN_SRC = (
    "from __future__ import annotations\n"
    "from typing import TypedDict, Literal\n"
    "from funcd_shim import CloudEvent, FunctionContext\n"
    "\n"
    "class FuncInput(TypedDict):\n"
    "    name: str\n"
    "    tier: Literal['free', 'pro']\n"
    "\n"
    "FuncOutput = None\n"  # void output (ADR-0090); focus is resolving the INPUT under future-annotations
    "def handle(ctx, event):\n"
    "    return None\n"
)


def test_build_resolves_types_under_future_annotations() -> None:
    r = build(_FUTURE_ANN_SRC)
    assert r.input_schema is not None, "future-annotations must not defeat schema generation"
    assert r.input_schema["properties"]["tier"]["enum"] == ["free", "pro"]
    assert r.input_schema["additionalProperties"] is False


# `FuncInput = Json` (the escape hatch) → the empty schema {} (accepts any JSON).
def test_build_json_input_is_empty_schema() -> None:
    src = (
        "from funcd_shim import CloudEvent, FunctionContext, Json\n"
        "FuncInput = Json\n"
        "FuncOutput = None\n"  # void output (ADR-0090); focus is the `Json` INPUT escape hatch
        "def handle(ctx, event):\n"
        "    return None\n"
    )
    assert build(src).input_schema == {}, "Json → the empty schema (any JSON)"


# ---- issue r23: a contract using the ADR-0058 profile formats builds, and its schema names them ----


@pytest.mark.parametrize(
    ("field", "fmt"),
    [
        ("uuid.UUID", "uuid"),
        ("Annotated[int, Field(json_schema_extra={'format': 'int32'})]", "int32"),
        ("Annotated[int, Field(json_schema_extra={'format': 'int64'})]", "int64"),
    ],
)
def test_issue_r23_profile_formats_build(field: str, fmt: str) -> None:
    src = (
        "import uuid\n"
        "from typing import Annotated\n"
        "from pydantic import BaseModel, Field\n"
        "class FuncInput(BaseModel):\n"
        f"    v: {field}\n"
        "FuncOutput = None\n"
        "def handle(ctx, event):\n"
        "    return None\n"
    )
    assert build(src).input_schema["properties"]["v"]["format"] == fmt


# ---- issue r44: each side keeps its own regex pattern and format ----


def test_issue_r44_each_side_keeps_its_regex_patterns() -> None:
    src = (
        "import datetime\n"
        "from typing import Annotated\n"
        "from pydantic import BaseModel, Field\n"
        "class FuncInput(BaseModel):\n"
        "    code: Annotated[str, Field(pattern='^[A-Z]+$')]\n"
        "class FuncOutput(BaseModel):\n"
        "    at: datetime.datetime\n"
        "def handle(ctx, event):\n"
        "    return None\n"
    )
    r = build(src)
    assert r.input_schema["properties"]["code"]["pattern"] == "^[A-Z]+$"
    assert r.output_schema["properties"]["at"]["format"] == "date-time"
