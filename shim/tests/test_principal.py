"""Scenario tests for oidc-shim-principal (funcd ADR-0335): funcd's edge sets four headers for a call it
signed in, and a handler reads ``context.principal`` from them. The pooled handler's test is in
test_pool.py."""

from __future__ import annotations

import asyncio
import base64
import http.client
import json
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from typing import Any

import pytest
from principals import CLAIMS, PRINCIPAL, encode, signed_in

from funcd_shim import shim
from funcd_shim.types import CloudEvent, FunctionContext


@contextmanager
def serve(handler: Any) -> Iterator[tuple[str, int]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), shim.make_request_handler(handler))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def call(address: tuple[str, int], headers: dict[str, str], data: Any = None) -> Any:
    """Send one call with *headers* and return the handler's answer; the call goes on, whatever they hold."""
    req = urllib.request.Request(
        f"http://{address[0]}:{address[1]}/",
        data=json.dumps({"data": data}).encode(),
        headers={"content-type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        return json.loads(resp.read())


def _who(context: FunctionContext, event: CloudEvent[Any]) -> dict[str, Any]:
    return {"principal": context.principal}


def test_scenario_oidc_shim_principal_signed_in() -> None:
    # scenario: oidc-shim-principal — a signed-in call gives the handler context.principal.
    with serve(_who) as address:
        assert call(address, signed_in()) == {"principal": PRINCIPAL}
        nameless = {k: v for k, v in signed_in().items() if k != "X-Funcd-Principal-Name"}
        assert call(address, nameless) == {"principal": {"provider": "sso", "id": "user-7", "claims": CLAIMS}}


def test_scenario_oidc_shim_principal_none_without_the_headers() -> None:
    with serve(_who) as address:
        assert call(address, {}) == {"principal": None}
        rest = {k: v for k, v in signed_in().items() if k != "X-Funcd-Principal"}
        assert call(address, rest) == {"principal": None}, "no X-Funcd-Principal, no principal"
        only = {"X-Funcd-Principal": signed_in()["X-Funcd-Principal"]}
        assert call(address, only) == {"principal": None}, "no X-Funcd-Principal-Id, no principal"


def _malformed() -> list[Any]:
    padded = encode({"provider": "sso", "claims": {"sub": "u"}})
    slashed = encode({"provider": "sso", "claims": {"sub": "???"}})
    assert padded.endswith("=")
    assert "/" in slashed
    values = {
        "no-base64": "not base64!",
        "base64-without-padding": padded.rstrip("="),
        "url-safe-base64": slashed.replace("/", "_"),
        "base64-with-a-stray-character": padded[:8] + "!" + padded[8:],
        "no-json": base64.b64encode(b"provider=sso").decode(),
        "no-utf8": base64.b64encode(b'{"provider": "s\xffo", "claims": {}}').decode(),
        "nan": base64.b64encode(b'{"provider": "sso", "claims": {"x": NaN}}').decode(),
        "json-array": encode([{"provider": "sso", "claims": CLAIMS}]),
        "no-provider": encode({"claims": CLAIMS}),
        "empty-provider": encode({"provider": "", "claims": CLAIMS}),
        "claims-no-object": encode({"provider": "sso", "claims": "sub=user-7"}),
    }
    return [pytest.param(value, id=name) for name, value in values.items()]


@pytest.mark.parametrize("value", _malformed())
def test_scenario_oidc_shim_principal_malformed_gives_none(value: str) -> None:
    # scenario: oidc-shim-principal — a malformed X-Funcd-Principal gives none and does not fail the call.
    with serve(_who) as address:
        assert call(address, {**signed_in(), "X-Funcd-Principal": value}) == {"principal": None}


def test_scenario_oidc_shim_principal_repeated_header_gives_none() -> None:
    with serve(_who) as address:
        conn = http.client.HTTPConnection(*address, timeout=5)
        try:
            conn.putrequest("POST", "/")
            for name, value in signed_in().items():
                conn.putheader(name, value)
            conn.putheader("X-Funcd-Principal", signed_in()["X-Funcd-Principal"])
            conn.putheader("Content-Length", "2")
            conn.endheaders(b"{}")
            resp = conn.getresponse()
            assert (resp.status, json.loads(resp.read())) == (200, {"principal": None})
        finally:
            conn.close()


def test_scenario_oidc_shim_principal_async_handler() -> None:
    async def who(context: FunctionContext, event: CloudEvent[Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"principal": context.principal}

    with serve(who) as address:
        assert call(address, signed_in()) == {"principal": PRINCIPAL}


def test_scenario_oidc_shim_principal_calls_that_overlap_read_their_own() -> None:
    first_waits, second_done = threading.Event(), threading.Event()

    def who(context: FunctionContext, event: CloudEvent[Any]) -> dict[str, Any]:
        if event.get("data") == "first":
            first_waits.set()
            second_done.wait(5)
        principal = context.principal
        return {"id": None if principal is None else principal["id"]}

    results: dict[str, Any] = {}
    with serve(who) as address:
        first = threading.Thread(
            target=lambda: results.update(first=call(address, signed_in("user-a"), "first"))
        )
        first.start()
        assert first_waits.wait(5)
        results["second"] = call(address, signed_in("user-b"), "second")
        results["none"] = call(address, {}, "none")
        second_done.set()
        first.join(5)
    assert results == {"first": {"id": "user-a"}, "second": {"id": "user-b"}, "none": {"id": None}}
