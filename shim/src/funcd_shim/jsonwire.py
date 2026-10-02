"""JSON on the shim's wire (ADR-0049): request bodies are read and response bodies written the way the
Node shim's ``JSON.parse`` and ``JSON.stringify`` do (ADR-0037), for the solo shim and the pool workers
alike. Python's ``json`` defaults differ: it reads and writes ``NaN``/``Infinity``, which JSON lacks."""

from __future__ import annotations

import json
import math
from typing import Any, NoReturn


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not valid JSON")


def decode(raw: bytes | str) -> Any:
    """Parse a request body. Raises ``ValueError`` for anything that is not JSON, ``NaN`` included."""
    return json.loads(raw, parse_constant=_reject_constant)


def encode(body: Any) -> bytes:
    """Encode a response body, writing ``null`` for NaN and ±Infinity. Raises ``TypeError`` or
    ``ValueError`` when *body* has no JSON form (a set, bytes, a circular reference)."""
    try:
        return json.dumps(body, allow_nan=False).encode()
    except ValueError:
        json.dumps(body)  # re-raises a circular reference, so only non-finite floats get past it
        return json.dumps(_finite(body)).encode()


def _finite(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(v) for v in value]
    return value
