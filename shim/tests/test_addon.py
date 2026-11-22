"""Tests for context.addon (funcd ADR-0338) over a fake worker-node local API: the request funcd gets, the
engine's answer whatever its status, a refusal of funcd, the pool member header and the CLIENT span."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import UnixStreamServer

import pytest

import funcd_shim._poolworker as pw
from funcd_shim.addon import AddonClient, AddonResponse
from funcd_shim.invcontext import InvContext, reset_inv, set_inv
from funcd_shim.shim import _Context
from funcd_shim.tracespan import new_inv_context

# Each request as (method, path, headers, body); the next reply as (status, headers, body).
_CALLS: list[tuple[str, str, list[tuple[str, str]], bytes]] = []
_REPLY: list[tuple[int, list[tuple[str, str]], bytes]] = []

_ENGINE = ("X-Funcd-Addon-Reply", "1")
_CALLER_TP = f"00-{'a' * 32}-{'b' * 16}-01"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        _CALLS.append((self.command, self.path, list(self.headers.items()), self.rfile.read(length)))
        status, headers, body = _REPLY[0]
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._serve()

    def do_PUT(self) -> None:
        self._serve()

    def log_message(self, *_args: object) -> None:
        pass


class _Lines:
    """A telemetry channel that keeps every line written to it."""

    def __init__(self) -> None:
        self.lines: list[dict[str, object]] = []

    def write_line(self, line: bytes) -> None:
        self.lines.append(json.loads(line))


@pytest.fixture
def api(sock_dir: Path) -> Iterator[None]:
    _CALLS.clear()
    _REPLY.clear()
    sock = str(sock_dir / "s")
    server = UnixStreamServer(sock, _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["FUNCD_INVOKE_SOCKET"] = sock
    try:
        yield
    finally:
        os.environ.pop("FUNCD_INVOKE_SOCKET", None)
        server.shutdown()
        server.server_close()


@pytest.fixture
def caller() -> Iterator[InvContext]:
    inv = new_inv_context(_CALLER_TP)
    token = set_inv(inv)
    try:
        yield inv
    finally:
        reset_inv(token)


def _engine_headers(resp: AddonResponse) -> list[tuple[str, str]]:
    """The answer's headers but those BaseHTTPRequestHandler adds to every answer."""
    return [h for h in resp.headers if h[0] not in ("server", "date")]


def _header(name: str) -> str | None:
    return next((v for k, v in _CALLS[0][2] if k.lower() == name), None)


@pytest.mark.usefixtures("api")
def test_scenario_addon_call() -> None:
    _REPLY.append(
        (
            200,
            [_ENGINE, ("Content-Type", "application/json"), ("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")],
            b"{}",
        )
    )
    resp = AddonClient().fetch(
        "idp",
        "/auth/v1/password_policy?full=1",
        method="PUT",
        headers={"Content-Type": "application/json", "X-Mine": "yes"},
        body=b'{"length_min":12}',
    )
    method, path, _, body = _CALLS[0]
    assert (method, path, body) == ("PUT", "/addon/idp/auth/v1/password_policy?full=1", b'{"length_min":12}')
    assert _header("x-mine") == "yes"
    assert _header("content-type") == "application/json"
    assert _header("content-length") == "17"
    assert resp.status == 200
    assert resp.body == b"{}"
    assert _engine_headers(resp) == [
        ("content-type", "application/json"),
        ("set-cookie", "a=1"),
        ("set-cookie", "b=2"),
    ]


@pytest.mark.usefixtures("api")
def test_an_answer_of_the_engine_is_returned_whatever_its_status() -> None:
    _REPLY.append((302, [_ENGINE, ("Location", "/elsewhere")], b""))
    resp = AddonClient().fetch("idp", "/auth/v1/x")
    assert (resp.status, _engine_headers(resp)) == (302, [("location", "/elsewhere")])
    _REPLY[0] = (500, [_ENGINE], b"engine failed")
    resp = AddonClient().fetch("idp", "/auth/v1/x")
    assert (resp.status, resp.body) == (500, b"engine failed")


@pytest.mark.usefixtures("api")
def test_scenario_addon_call_refused() -> None:
    problem = (
        b'{"type":"about:blank","title":"Forbidden","status":403,"detail":"no add-on object as \\"other\\""}'
    )
    _REPLY.append((403, [("Content-Type", "application/problem+json")], problem))
    with pytest.raises(
        RuntimeError, match=r'context\.addon\.fetch\("other"\) failed: 403 .*no add-on object'
    ):
        AddonClient().fetch("other", "/auth/v1/x")


@pytest.mark.usefixtures("api")
def test_scenario_addon_call_held() -> None:
    _REPLY.append((503, [("Retry-After", "30")], b'{"status":503,"detail":"namespace team is held"}'))
    with pytest.raises(RuntimeError, match=r"503 .*namespace team is held.*\(Retry-After: 30\)$"):
        AddonClient().fetch("idp", "/auth/v1/x")


@pytest.mark.usefixtures("api")
def test_the_member_and_the_traceparent_are_the_shims(caller: InvContext) -> None:
    _REPLY.append((200, [_ENGINE], b""))
    AddonClient("front").fetch(
        "idp", "/auth/v1/x", headers={"x-funcd-member": "other", "Traceparent": "forged"}
    )
    names = [k.lower() for k, _ in _CALLS[0][2]]
    assert names.count("x-funcd-member") == 1
    assert names.count("traceparent") == 1
    assert _header("x-funcd-member") == "front"
    traceparent = _header("traceparent")
    assert traceparent is not None
    assert traceparent.startswith(f"00-{caller.trace_id}-")


@pytest.mark.usefixtures("api")
def test_the_contexts_give_the_addon_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _REPLY.append((200, [_ENGINE], b""))
    _Context().addon.fetch("idp", "/auth/v1/x")
    assert _header("x-funcd-member") is None
    monkeypatch.setattr(pw, "_member", "front")
    _CALLS.clear()
    pw._Ctx().addon.fetch("idp", "/auth/v1/x")
    assert _header("x-funcd-member") == "front"


@pytest.mark.usefixtures("api", "caller")
def test_the_client_span_is_error_on_an_engine_5xx_and_a_refusal() -> None:
    ch = _Lines()
    for status, headers in ((200, [_ENGINE]), (502, [_ENGINE]), (404, [])):
        _REPLY[:] = [(status, headers, b"")]
        try:
            AddonClient(channel=ch).fetch("idp", "/auth/v1/x")
        except RuntimeError:
            pass
    spans = [line for line in ch.lines if line.get("name") == "call idp"]
    assert [(s["kind"], s["status"], s["attrs"]) for s in spans] == [
        ("CLIENT", "OK", {"http.status_code": "200"}),
        ("CLIENT", "ERROR", {"http.status_code": "502"}),
        ("CLIENT", "ERROR", {"http.status_code": "404"}),
    ]


def test_a_call_needs_the_socket_and_a_path_from_the_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FUNCD_INVOKE_SOCKET", raising=False)
    with pytest.raises(RuntimeError, match="FUNCD_INVOKE_SOCKET unset"):
        AddonClient().fetch("idp", "/auth/v1/x")
    monkeypatch.setenv("FUNCD_INVOKE_SOCKET", "/nonexistent")
    with pytest.raises(ValueError, match='must start with "/"'):
        AddonClient().fetch("idp", "auth/v1/x")
