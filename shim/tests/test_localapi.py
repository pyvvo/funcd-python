"""funcd ADR-0346: a worker in a container under funcdctl dev reaches the local API on a listener of its
own, ``FUNCD_INVOKE_URL``, sending ``FUNCD_INVOKE_TOKEN`` in a header (Decision 6), and with
``FUNCD_LOG_STDOUT`` set and no log channel its records print to stdout (Decision 4)."""

import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from funcd_shim.invoke import TOKEN_HEADER, invoke, local_api
from funcd_shim.kv import KVClient

_SEEN: list[tuple[str, str, str | None]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _answer(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        _SEEN.append((self.command, self.path, self.headers.get(TOKEN_HEADER)))
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    do_GET = do_POST = _answer  # noqa: N815 - stdlib names

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def listener(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    _SEEN.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    monkeypatch.setenv("FUNCD_INVOKE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("FUNCD_INVOKE_TOKEN", "secret-token")
    monkeypatch.setenv("FUNCD_INVOKE_SOCKET", "/nonexistent/invoke.sock")
    try:
        yield port
    finally:
        server.shutdown()


def test_local_api_prefers_the_url_to_the_socket(listener: int) -> None:
    conn = local_api()
    assert conn is not None
    assert (conn.host, conn.port) == ("127.0.0.1", listener)


def test_local_api_is_the_socket_without_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FUNCD_INVOKE_URL", raising=False)
    monkeypatch.setenv("FUNCD_INVOKE_SOCKET", "/run/funcd/invoke.sock")
    conn = local_api()
    assert conn is not None
    assert getattr(conn, "_socket_path", None) == "/run/funcd/invoke.sock"
    monkeypatch.delenv("FUNCD_INVOKE_SOCKET")
    assert local_api() is None


def test_the_clients_send_the_token_to_the_url(listener: int) -> None:
    assert invoke("pricing", {"item": 1}) == {"ok": True}
    KVClient().get("counter", "a")
    assert [(m, p) for m, p, _ in _SEEN] == [("POST", "/invoke/pricing"), ("GET", "/kv/counter/a")]
    assert all(token == "secret-token" for _, _, token in _SEEN)  # noqa: S105 - a test value


def test_log_stdout_prints_each_record() -> None:
    script = (
        "import logging\n"
        "from funcd_shim.funclog import install_log_capture\n"
        "install_log_capture()\n"
        "logging.info('hello %s', 42)\n"
        "logging.warning('careful')\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("FUNCD_LOG_FD", "FUNCD_LOG_SOCK")}
    env["FUNCD_LOG_STDOUT"] = "1"
    run = subprocess.run(  # noqa: S603 - this interpreter, on the test's own script
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=False
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout == "hello 42\ncareful\n"
    assert "careful" not in run.stderr
