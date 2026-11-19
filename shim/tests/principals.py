"""The headers funcd's edge sets for a signed-in call (funcd ADR-0335), for the solo and pooled tests."""

import base64
import json
from typing import Any

CLAIMS = {
    "iss": "https://login.example.com",
    "sub": "user-7",
    "aud": "app",
    "name": "Zoë Ångström",
    "email": "zoe@example.com",
}
PRINCIPAL = {"provider": "sso", "id": "user-7", "name": "Zoë Ångström", "claims": CLAIMS}


def encode(value: Any) -> str:
    return base64.b64encode(json.dumps(value).encode()).decode()


def as_sent(text: str) -> str:
    """A header value whose bytes are the UTF-8 form of *text*: http.client writes each character of a str
    value as one Latin-1 byte."""
    return text.encode().decode("latin-1")


def signed_in(sub: str = "user-7") -> dict[str, str]:
    """The headers of a browser session of the user *sub*."""
    return {
        "X-Funcd-Principal": encode({"provider": "sso", "claims": {**CLAIMS, "sub": sub}}),
        "X-Funcd-Principal-Id": sub,
        "X-Funcd-Principal-Name": as_sent("Zoë Ångström"),
        "X-Funcd-Id-Token": "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyLTcifQ.c2lnbmF0dXJl",
    }
