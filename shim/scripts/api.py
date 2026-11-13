"""api.py writes api.json: the handler API that funcd's guide shows on each feature's Spec tab.

It reads FunctionContext in funcd_shim.types: each client is one of its properties (ctx.kv), and the
context's own methods are the client named "". Run it with ``uv run python scripts/api.py``;
tests/test_api.py fails when api.json is stale.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from pathlib import Path

from funcd_shim import blob, config, kv, lake, secret, sql, types

API = Path(__file__).resolve().parent.parent / "api.json"

# types.py imports the client classes only for type checking, so a property's return annotation is
# resolved here.
CLIENTS = {
    name: cls
    for module in (blob, config, kv, lake, secret, sql)
    for name, cls in vars(module).items()
    if inspect.isclass(cls) and name.endswith("Client")
}


def _flat(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _annotation(annotation: object) -> str:
    return annotation if isinstance(annotation, str) else inspect.formatannotation(annotation)


def _signature(name: str, fn: Callable[..., object]) -> str:
    sig = inspect.signature(fn)
    parts: list[str] = []
    keyword_only = False
    for p in list(sig.parameters.values())[1:]:
        if p.kind is p.KEYWORD_ONLY and not keyword_only:
            parts.append("*")
            keyword_only = True
        text = f"*{p.name}" if p.kind is p.VAR_POSITIONAL else p.name
        if p.annotation is not p.empty:
            text += f": {_annotation(p.annotation)}"
        if p.default is not p.empty:
            text += f" = {p.default!r}"
        parts.append(text)
    returns = "" if sig.return_annotation is sig.empty else f" -> {_annotation(sig.return_annotation)}"
    return f"{name}({', '.join(parts)}){returns}"


def _methods(cls: type) -> list[dict[str, str]]:
    return [
        {"name": name, "signature": _signature(name, member), "doc": _flat(inspect.getdoc(member))}
        for name, member in vars(cls).items()
        if inspect.isfunction(member) and not name.startswith("_")
    ]


def api() -> dict[str, list[dict[str, object]]]:
    context = types.FunctionContext
    clients: list[dict[str, object]] = [
        {
            "name": "",
            "type": "FunctionContext",
            "doc": _flat(inspect.getdoc(context)),
            "methods": _methods(context),
        }
    ]
    for name, member in vars(context).items():
        if isinstance(member, property) and member.fget is not None:
            client = CLIENTS[member.fget.__annotations__["return"]]
            clients.append(
                {
                    "name": name,
                    "type": client.__name__,
                    "doc": _flat(inspect.getdoc(member)),
                    "methods": _methods(client),
                }
            )
    return {"clients": clients}


if __name__ == "__main__":
    API.write_text(json.dumps(api(), indent=2) + "\n")
