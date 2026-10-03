"""JSON on the shim's wire (ADR-0049): request bodies are read and response bodies written the way the
Node shim's ``JSON.parse`` and ``JSON.stringify`` do (ADR-0037), for the solo shim and the pool workers
alike. Python's ``json`` defaults differ: it reads and writes ``NaN``/``Infinity``, which JSON lacks,
and writes floats with Python's repr (``1.0``, ``1e-05``) where ``JSON.stringify`` writes ``1`` and
``0.00001``."""

from __future__ import annotations

import json
import json.encoder
import math
from typing import Any, NoReturn

_unencodable = json.JSONEncoder().default


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not valid JSON")


def decode(raw: bytes | str) -> Any:
    """Parse a request body. Raises ``ValueError`` for anything that is not JSON, ``NaN`` included."""
    return json.loads(raw, parse_constant=_reject_constant)


def encode(body: Any) -> bytes:
    """Encode a response body, writing numbers as ``JSON.stringify`` does and ``null`` for NaN and
    ±Infinity. Raises ``TypeError`` or ``ValueError`` when *body* has no JSON form (a set, bytes, a
    circular reference)."""
    # json's C encoder always writes floats with float.__repr__; only its Python encoder takes a formatter
    chunks = json.encoder._make_iterencode(  # type: ignore[attr-defined]
        {}, _unencodable, json.encoder.encode_basestring, None, _number, ":", ",", False, False, True
    )
    text = "".join(chunks(body, 0))
    # a lone surrogate has no UTF-8 form: JSON.stringify and backslashreplace both write it as \uXXXX
    return text.encode("utf-8", "backslashreplace")


def _number(value: float) -> str:
    """ECMAScript ``Number::toString``: the shortest round-trip digits, which Python's float repr also
    picks, as a plain decimal for a decimal exponent *n* in (-6, 21] and in exponent form otherwise."""
    if not math.isfinite(value):
        return "null"
    if value == 0:
        return "0"
    mantissa, _, exponent = float.__repr__(abs(value)).partition("e")
    whole, _, fraction = mantissa.partition(".")
    padded = whole + fraction
    digits = padded.lstrip("0")
    n = len(whole) + int(exponent or 0) - (len(padded) - len(digits))
    digits = digits.rstrip("0")
    k = len(digits)
    sign = "-" if value < 0 else ""
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * -n + digits
    return sign + digits[0] + ("." + digits[1:] if k > 1 else "") + f"e{n - 1:+d}"
