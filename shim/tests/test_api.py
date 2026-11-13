"""api.json is what scripts/api.py writes from FunctionContext."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

SHIM = Path(__file__).resolve().parent.parent


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("api", SHIM / "scripts" / "api.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_json_is_fresh() -> None:
    committed = json.loads((SHIM / "api.json").read_text())
    assert committed == _generator().api(), "api.json is stale: run uv run python scripts/api.py"


def test_api_json_lists_every_kv_method() -> None:
    clients = _generator().api()["clients"]
    kv = next(c for c in clients if c["name"] == "kv")
    assert kv["type"] == "KVClient"
    assert [m["name"] for m in kv["methods"]] == [
        "get",
        "get_entry",
        "get_str",
        "get_json",
        "put",
        "put_if_absent",
        "compare_and_set",
        "delete",
        "delete_if",
        "list",
    ]
