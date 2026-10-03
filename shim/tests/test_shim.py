"""HTTP-contract tests for the Python shim (ADR-0049 py-shim-contract / py-contract-* / py-shape-gate).

The wire contract is exercised two ways: in-process via a threaded server built from
``make_request_handler`` (fast, covers the status mapping), and end-to-end via a real
``python -m funcd_shim`` subprocess (the launch path the Go process driver uses — covers the
FUNCD_PORTFILE handshake and the shape-gate exit codes)."""

from __future__ import annotations

import asyncio
import datetime
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import closing, contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from funcd_shim import contract, runtime, shim
from funcd_shim.types import CloudEvent, FunctionContext, Validator

ECHO = (
    "def handle(context, event):\n"
    "    context.log('hi', event.get('data'))\n"
    "    return {'echoed': event.get('data')}\n"
)


# fake precompiled validators (stand-ins for what pydantic models produce via resolve_validators).
def hello_input(data: Any) -> list[Any]:
    ok = isinstance(data, dict) and isinstance(data.get("hello"), str)
    return [] if ok else [{"msg": "hello must be a string"}]


def ok_output(result: Any) -> list[Any]:
    ok = isinstance(result, dict) and isinstance(result.get("ok"), bool)
    return [] if ok else [{"msg": "ok must be a boolean"}]


def void_output(result: Any) -> list[Any]:
    return [] if result is None else [{"msg": "expected no body"}]


@contextmanager
def serve(handler: Any, validators: runtime.Validators | None = None) -> Iterator[str]:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), shim.make_request_handler(handler, validators or runtime.Validators())
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(base: str, body: bytes | str) -> tuple[int, bytes]:
    data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(base + "/", data=data, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - local loopback
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def get(base: str, path: str) -> int:
    try:
        with urllib.request.urlopen(base + path, timeout=5) as resp:  # noqa: S310 - local loopback
            return int(resp.status)
    except urllib.error.HTTPError as err:
        return int(err.code)


def _echo(context: FunctionContext, event: CloudEvent[Any]) -> dict[str, Any]:
    return {"echoed": event.get("data")}


def test_handler_returns_object_200() -> None:
    with serve(_echo) as base:
        status, body = post(base, json.dumps({"data": {"hello": "world"}}))
        assert status == 200
        assert json.loads(body) == {"echoed": {"hello": "world"}}


def test_handler_returns_none_204() -> None:
    with serve(lambda ctx, event: None) as base:
        status, body = post(base, json.dumps({"data": 1}))
        assert status == 204
        assert body == b""


def test_handler_raises_500() -> None:
    def boom(ctx: FunctionContext, event: CloudEvent[Any]) -> Any:
        raise RuntimeError("kaboom")

    with serve(boom) as base:
        status, body = post(base, "{}")
        assert status == 500
        assert json.loads(body)["error"] == "kaboom"


def test_issue_188_async_handler_is_awaited() -> None:
    async def echo(ctx: FunctionContext, event: CloudEvent[Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"echoed": event.get("data")}

    async def boom(ctx: FunctionContext, event: CloudEvent[Any]) -> Any:
        await asyncio.sleep(0)
        raise RuntimeError("async kaboom")

    with serve(echo) as base:
        status, body = post(base, json.dumps({"data": {"hello": "world"}}))
        assert status == 200
        assert json.loads(body) == {"echoed": {"hello": "world"}}
    with serve(boom) as base:
        status, body = post(base, "{}")
        assert status == 500
        assert json.loads(body)["error"] == "async kaboom"


def test_invalid_json_400() -> None:
    with serve(_echo) as base:
        status, _ = post(base, "{not json")
        assert status == 400


def test_empty_body_is_empty_event() -> None:
    with serve(_echo) as base:
        status, body = post(base, "")
        assert status == 200
        assert json.loads(body) == {"echoed": None}


def test_health_endpoints() -> None:
    with serve(_echo) as base:
        assert get(base, "/health/liveness") == 200
        assert get(base, "/health/readiness") == 200
        assert get(base, "/nope") == 404


def test_input_contract_valid_passes() -> None:
    with serve(_echo, runtime.Validators(input=hello_input)) as base:
        status, _ = post(base, json.dumps({"data": {"hello": "world"}}))
        assert status == 200


def test_input_contract_mismatch_422_and_handler_not_called() -> None:
    called = {"n": 0}

    def counting(ctx: FunctionContext, event: CloudEvent[Any]) -> Any:
        called["n"] += 1
        return {}

    with serve(counting, runtime.Validators(input=hello_input)) as base:
        status, body = post(base, json.dumps({"data": {"hello": 5}}))
        assert status == 422
        payload = json.loads(body)
        assert payload["error"] == "event data does not match the input contract"
        assert payload["details"]
        assert called["n"] == 0


def test_output_contract_mismatch_500() -> None:
    with serve(lambda ctx, e: {"wrong": True}, runtime.Validators(output=ok_output)) as base:
        status, body = post(base, json.dumps({"data": {}}))
        assert status == 500
        payload = json.loads(body)
        assert payload["error"] == "handler result does not match the output contract"
        assert payload["details"]


def test_void_output_contract_empty_204_nonempty_500() -> None:
    with serve(lambda ctx, e: None, runtime.Validators(output=void_output)) as base:
        assert post(base, "{}")[0] == 204
    with serve(lambda ctx, e: {"surprise": True}, runtime.Validators(output=void_output)) as base:
        assert post(base, "{}")[0] == 500


def test_json_input_accepts_anything() -> None:
    json_input: Validator = lambda data: []  # noqa: E731 - generated from FuncInput = Json ({})
    with serve(_echo, runtime.Validators(input=json_input)) as base:
        status, _ = post(base, json.dumps({"data": ["literally", 1, True]}))
        assert status == 200


def test_no_contract_unvalidated() -> None:
    with serve(_echo) as base:
        status, _ = post(base, json.dumps({"data": "anything goes"}))
        assert status == 200


def test_keep_alive_no_desync() -> None:
    # HTTP/1.1 keep-alive: many requests on ONE connection, including a 404 POST WITH a body, must
    # not desync — the body is drained before the early return, so the next request lines up.
    import http.client

    with serve(_echo) as base:
        conn = http.client.HTTPConnection(base.removeprefix("http://"), timeout=5)
        try:
            conn.request("POST", "/", b'{"data":{"hello":"world"}}')
            r = conn.getresponse()
            assert r.status == 200
            assert json.loads(r.read())["echoed"] == {"hello": "world"}

            conn.request("POST", "/nope", b'{"junk":true}')  # 404 WITH a body
            r = conn.getresponse()
            assert r.status == 404
            r.read()

            conn.request("POST", "/", b'{"data":42}')  # same connection still parses correctly
            r = conn.getresponse()
            assert r.status == 200
            assert json.loads(r.read())["echoed"] == 42
        finally:
            conn.close()


def _circular() -> dict[str, Any]:
    d: dict[str, Any] = {}
    d["self"] = d
    return d


@pytest.mark.parametrize(
    "result",
    [{1}, datetime.datetime(2026, 1, 1), b"x", _circular(), lambda: 0],
    ids=["set", "datetime", "bytes", "circular", "lambda"],
)
def test_issue_131_unencodable_result_returns_500(result: Any) -> None:
    with serve(lambda ctx, e: result) as base:
        status, body = post(base, "{}")
        assert status == 500
        assert json.loads(body)["error"]


@pytest.mark.parametrize("exc", [SystemExit(3), KeyboardInterrupt()], ids=["SystemExit", "KeyboardInterrupt"])
def test_issue_131_base_exception_returns_500(exc: BaseException) -> None:
    def raising(ctx: FunctionContext, event: CloudEvent[Any]) -> Any:
        raise exc

    with serve(raising) as base:
        status, body = post(base, "{}")
        assert status == 500
        assert json.loads(body) == {"error": str(exc)}


def test_issue_131_non_finite_result_written_as_null() -> None:
    # JSON has no NaN/Infinity; the Node shim's JSON.stringify writes null for them.
    result = {"x": float("nan"), "y": [float("inf"), -float("inf")], "z": 1.5}
    with serve(lambda ctx, e: result) as base:
        status, body = post(base, "{}")
        assert status == 200
        assert json.loads(body) == {"x": None, "y": [None, None], "z": 1.5}


def test_issue_r21_response_json_is_written_like_json_stringify() -> None:
    # Expected bytes are what the Node shim's JSON.stringify writes for the same values (ADR-0049 §2).
    result = {"a": [1, 2], "s": "é", "c": "\x01\n", "u": "\udc80"}
    with serve(lambda ctx, e: result) as base:
        assert post(base, "{}") == (200, '{"a":[1,2],"s":"é","c":"\\u0001\\n","u":"\\udc80"}'.encode())
    with serve(lambda ctx, e: {"x": float("nan"), "s": "é"}) as base:
        assert post(base, "{}") == (200, '{"x":null,"s":"é"}'.encode())

    def boom(ctx: FunctionContext, event: CloudEvent[Any]) -> Any:
        raise RuntimeError("échec")

    with serve(boom) as base:
        assert post(base, "{}") == (500, '{"error":"échec"}'.encode())
    mismatch = (
        b'{"error":"event data does not match the input contract",'
        b'"details":[{"msg":"hello must be a string"}]}'
    )
    with serve(_echo, runtime.Validators(input=hello_input)) as base:
        assert post(base, '{"data": {"hello": 5}}') == (422, mismatch)


def test_issue_r43_response_floats_are_written_like_json_stringify() -> None:
    # Expected bytes are what the Node shim's JSON.stringify writes for the same values (ADR-0049 §2).
    floats = [1.0, -0.0, 1e-05, 1e-07, 1e16, 1.2345678901234568e20, 1e21, 0.1, -2.5, 1.5e-10]
    with serve(lambda ctx, e: {"v": floats, "n": 3}) as base:
        assert post(base, "{}") == (
            200,
            b'{"v":[1,0,0.00001,1e-7,10000000000000000,123456789012345680000,1e+21,0.1,-2.5,1.5e-10],"n":3}',
        )


@pytest.mark.parametrize(
    "validators",
    [runtime.Validators(), runtime.Validators(input=lambda data: [])],
    ids=["no-contract", "json-contract"],
)
@pytest.mark.parametrize(
    "body", ["NaN", '{"data": NaN}', '{"data": [Infinity]}', '{"data": {"v": -Infinity}}']
)
def test_issue_131_non_finite_request_returns_400(validators: runtime.Validators, body: str) -> None:
    with serve(_echo, validators) as base:
        assert post(base, body)[0] == 400


@pytest.mark.parametrize("body", ["null", "[1, 2]", "42", '"s"', "true"])
def test_issue_131_non_object_body_returns_400(body: str) -> None:
    with serve(_echo, runtime.Validators(input=lambda data: [])) as base:
        status, payload = post(base, body)
        assert status == 400
        assert payload == b"request body must be a JSON object (CloudEvent envelope)"


# ---- ADR-0123: runtime-compiled validators (from FUNCD_CONTRACT_PATH) enforce the same wire ----


def test_runtime_compiled_validators_enforce_wire(tmp_path: Path) -> None:
    # scenario: runtime-compiles-validator — validators compiled from the delivered schema (not a
    # baked callable) enforce input→422 / output→500 / void→204, byte-identical to the baked path.
    blob = tmp_path / "c.json"
    blob.write_text(
        json.dumps(
            {
                "input": {
                    "type": "object",
                    "properties": {"hello": {"type": "string"}},
                    "required": ["hello"],
                    "additionalProperties": False,
                },
                "output": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            }
        )
    )
    validators = contract.load_from_path(str(blob))

    # bad input → 422 (handler not called)
    with serve(lambda ctx, e: {"ok": True}, validators) as base:
        assert post(base, json.dumps({"data": {"hello": 5}}))[0] == 422
    # good input, bad output → 500
    with serve(lambda ctx, e: {"wrong": True}, validators) as base:
        assert post(base, json.dumps({"data": {"hello": "hi"}}))[0] == 500
    # good input, good output → 200
    with serve(lambda ctx, e: {"ok": True}, validators) as base:
        assert post(base, json.dumps({"data": {"hello": "hi"}}))[0] == 200

    # void output side → 204 on empty, 500 on non-empty
    (blob.parent / "v.json").write_text(json.dumps({"input": {}, "output": {"type": "null"}}))
    void = contract.load_from_path(str(blob.parent / "v.json"))
    with serve(lambda ctx, e: None, void) as base:
        assert post(base, "{}")[0] == 204
    with serve(lambda ctx, e: {"surprise": True}, void) as base:
        assert post(base, "{}")[0] == 500


# ---- main() shape-gate exit codes (return before serving) ----


def test_main_missing_artifact_exit_2(monkeypatch: Any) -> None:
    monkeypatch.delenv("FUNCD_ARTIFACT", raising=False)
    assert shim.main([]) == 2


def test_main_bad_handler_exit_3(monkeypatch: Any, tmp_path: Path) -> None:
    art = tmp_path / "a.py"
    art.write_text("x = 1\n")  # no handle export
    monkeypatch.setenv("FUNCD_ARTIFACT", str(art))
    assert shim.main([]) == 3


# ---- real subprocess: the launch path the Go process driver uses (portfile handshake + e2e) ----


def test_subprocess_portfile_handshake_and_contract(tmp_path: Path) -> None:
    artifact = tmp_path / "fn.py"
    artifact.write_text(ECHO)
    port_file = tmp_path / "fn.port"
    proc = subprocess.Popen(
        [sys.executable, "-m", "funcd_shim"],
        env={
            "FUNCD_ARTIFACT": str(artifact),
            "FUNCD_PORTFILE": str(port_file),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not (port_file.exists() and port_file.read_text().strip()):
            time.sleep(0.05)
        assert port_file.exists() and port_file.read_text().strip(), "shim never wrote its port"
        port = int(port_file.read_text().strip())
        # it bound loopback (process mode), not 0.0.0.0
        with closing(socket.create_connection(("127.0.0.1", port), timeout=5)):
            pass
        status, body = post(f"http://127.0.0.1:{port}", json.dumps({"data": {"k": "v"}}))
        assert status == 200
        assert json.loads(body) == {"echoed": {"k": "v"}}
    finally:
        proc.terminate()
        proc.wait(timeout=5)
