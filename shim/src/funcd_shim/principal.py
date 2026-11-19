"""The principal of a signed-in call (funcd ADR-0335).

funcd's edge sets four headers for a call it signed in and drops any copy a caller sent, so the shim builds
``context.principal`` from those headers alone. A value the edge does not write gives no principal and never
fails the call. Stdlib only.
"""

from __future__ import annotations

import base64
from email.message import Message
from typing import TYPE_CHECKING

from . import jsonwire

if TYPE_CHECKING:
    from .types import Principal

PRINCIPAL_HEADER = "X-Funcd-Principal"
ID_HEADER = "X-Funcd-Principal-Id"
NAME_HEADER = "X-Funcd-Principal-Name"


def _one(headers: Message, name: str) -> str:
    """The header's value without its surrounding blanks, or "" when it is absent or repeated, as the
    TypeScript shim reads it (Node joins a repeated header, which then fails)."""
    values = headers.get_all(name) or []
    return values[0].strip(" \t") if len(values) == 1 else ""


def _utf8(value: str) -> str:
    """The text that a header's bytes spell in UTF-8: http.server reads each byte as one Latin-1 character,
    and the edge writes the values as UTF-8."""
    return value.encode("latin-1").decode("utf-8", "replace")


def principal_from(headers: Message) -> Principal | None:
    """Build a call's principal from its headers: ``"provider"`` and ``"claims"`` from X-Funcd-Principal, the
    standard Base64 of the JSON ``{provider, claims}``, ``"id"`` from X-Funcd-Principal-Id and ``"name"``
    from X-Funcd-Principal-Name. None when X-Funcd-Principal or X-Funcd-Principal-Id is absent or empty, or
    when X-Funcd-Principal is not that encoding of an object with a provider name and an object of claims."""
    encoded, sub = _one(headers, PRINCIPAL_HEADER), _one(headers, ID_HEADER)
    if not encoded or not sub:
        return None
    try:
        value = jsonwire.decode(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except ValueError:  # binascii.Error, UnicodeDecodeError and json's errors are all ValueErrors
        return None
    if not isinstance(value, dict):
        return None
    provider, claims = value.get("provider"), value.get("claims")
    if not isinstance(provider, str) or not provider or not isinstance(claims, dict):
        return None
    principal: Principal = {"provider": provider, "id": _utf8(sub), "claims": claims}
    if name := _one(headers, NAME_HEADER):
        principal["name"] = _utf8(name)
    return principal
