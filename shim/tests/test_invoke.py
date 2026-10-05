"""Tests for the fn-to-fn ``context.invoke`` client (ADR-0064) and its CLIENT span (ADR-0165)."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import UnixStreamServer

import pytest

from funcd_shim.invcontext import InvContext, reset_inv, set_inv
from funcd_shim.invoke import invoke
from funcd_shim.tracespan import new_inv_context
from funcd_shim.types import FunctionContext


@pytest.mark.parametrize(
    "doc", [invoke.__doc__, FunctionContext.invoke.__doc__], ids=["invoke", "FunctionContext.invoke"]
)
def test_issue_r18_invoke_docs_state_data_key_unwrap(doc: str | None) -> None:
    # The broker reads an input with a top-level data/specversion key as a full envelope and hands the
    # callee only its data (funcd ADR-0134), so the docs must state the rule and the explicit-envelope escape.
    assert doc is not None
    assert '"data"' in doc
    assert '"specversion"' in doc
    assert '{"specversion": "1.0", "data": payload}' in doc


# The stub local API's reply (status, body, delay in seconds) and the traceparent of each request it got.
_REPLY: dict[str, object] = {}
_TRACEPARENTS: list[str | None] = []

_CALLER_TP = f"00-{'a' * 32}-{'b' * 16}-01"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # close per response → the client reads to EOF

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length", "0")))
        _TRACEPARENTS.append(self.headers.get("traceparent"))
        time.sleep(float(str(_REPLY.get("delay", 0))))
        self.send_response(int(str(_REPLY.get("status", 200))))
        self.end_headers()
        self.wfile.write(str(_REPLY.get("body", "{}")).encode("utf-8"))

    def log_message(self, *_args: object) -> None:  # silence the test server
        pass


class _Lines:
    """A telemetry channel that keeps every line written to it."""

    def __init__(self) -> None:
        self.lines: list[dict[str, object]] = []

    def write_line(self, line: bytes) -> None:
        self.lines.append(json.loads(line))


@pytest.fixture
def api(sock_dir: Path) -> Iterator[None]:
    _REPLY.clear()
    _TRACEPARENTS.clear()
    sock = str(sock_dir / "s")
    server = UnixStreamServer(sock, _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
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


@pytest.mark.usefixtures("api")
def test_client_span_stamps_traceparent_and_emits_client(caller: InvContext) -> None:
    ch = _Lines()
    _REPLY.update(status=200, body='{"ok": true}')
    assert invoke("greeter", {"name": "x"}, channel=ch) == {"ok": True}
    assert len(ch.lines) == 1
    span = ch.lines[0]
    assert _TRACEPARENTS == [f"00-{caller.trace_id}-{span['span_id']}-01"]
    assert span["span_id"] != caller.span_id
    assert {k: v for k, v in span.items() if k not in ("span_id", "start", "end")} == {
        "funcd.signal": "traces",
        "trace_id": caller.trace_id,
        "parent_id": caller.span_id,
        "name": "call greeter",
        "kind": "CLIENT",
        "status": "OK",
        "status_msg": "",
        "attrs": {"http.status_code": "200"},
        "inv": caller.inv,
        "links": [],
    }


@pytest.mark.usefixtures("api", "caller")
def test_failed_call_emits_error_client_span() -> None:
    ch = _Lines()
    _REPLY.update(status=422, body="event data does not match the input contract", delay=0.05)
    with pytest.raises(RuntimeError, match="failed: 422 ") as err:
        invoke("greeter", {}, channel=ch)
    span = ch.lines[0]
    assert span["kind"] == "CLIENT"
    assert span["status"] == "ERROR"
    assert span["status_msg"] == str(err.value)
    assert span["attrs"] == {"http.status_code": "422"}
    assert int(str(span["end"])) - int(str(span["start"])) >= 50_000_000  # the span covers the stub's delay


@pytest.mark.usefixtures("api")
def test_outside_an_invocation_no_traceparent_and_no_span() -> None:
    ch = _Lines()
    invoke("greeter", {}, channel=ch)
    assert _TRACEPARENTS == [None]
    assert ch.lines == []


@pytest.mark.usefixtures("api")
def test_no_channel_sends_traceparent_and_writes_no_span(caller: InvContext) -> None:
    invoke("greeter", {})
    tp = _TRACEPARENTS[0]
    assert tp is not None
    assert tp.startswith(f"00-{caller.trace_id}-")
    assert tp.endswith("-01")


@pytest.mark.usefixtures("caller")
def test_unset_socket_emits_error_client_span_without_status_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FUNCD_INVOKE_SOCKET", raising=False)
    ch = _Lines()
    with pytest.raises(RuntimeError, match="FUNCD_INVOKE_SOCKET unset"):
        invoke("greeter", {}, channel=ch)
    span = ch.lines[0]
    assert span["status"] == "ERROR"
    assert "FUNCD_INVOKE_SOCKET unset" in str(span["status_msg"])
    assert span["attrs"] == {}


@pytest.mark.usefixtures("api", "caller")
def test_non_json_2xx_emits_error_client_span_with_status_code() -> None:
    ch = _Lines()
    _REPLY.update(status=200, body="not json")
    with pytest.raises(json.JSONDecodeError):
        invoke("greeter", {}, channel=ch)
    span = ch.lines[0]
    assert span["status"] == "ERROR"
    assert span["attrs"] == {"http.status_code": "200"}


@pytest.mark.usefixtures("api", "caller")
def test_pool_member_client_span_carries_member() -> None:
    ch = _Lines()
    invoke("greeter", {}, member="front", channel=ch)
    span = ch.lines[0]
    assert span["kind"] == "CLIENT"
    assert span["funcd.member"] == "front"


@pytest.mark.usefixtures("api", "caller")
def test_long_error_reply_client_span_is_cut_at_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUNCD_FUNCLOG_MAX_RECORD_BYTES", "2048")
    ch = _Lines()
    _REPLY.update(status=422, body="x" * 10_000)
    with pytest.raises(RuntimeError) as err:
        invoke("greeter", {}, channel=ch)
    span = ch.lines[0]
    attrs = span["attrs"]
    assert isinstance(attrs, dict)
    assert span["kind"] == "CLIENT"
    assert attrs["http.status_code"] == "422"
    assert attrs["truncated"] == "true"
    assert int(attrs["keptBytes"]) == len(str(span["status_msg"]).encode("utf-8"))
    assert str(err.value).startswith(str(span["status_msg"]))
    assert len(json.dumps(span, separators=(",", ":"))) <= 2048
