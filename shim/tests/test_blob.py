"""Scenario tests for context.blob (ADR-0127): get/put/delete/list/signed_url over a minimal fake
worker-node local API (HTTP-over-UDS), proving the client wire without a real platform."""

import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import UnixStreamServer

import pytest

from funcd_shim.blob import BlobClient

# GET path -> (status, body). PUT/DELETE always answer 204. Every request is recorded in _RECORDED.
_ROUTES: dict[str, tuple[int, bytes]] = {}
_RECORDED: list[tuple[str, str, bytes]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # close per response → the client reads to EOF

    def _record(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""
        _RECORDED.append((self.command, self.path, body))

    def do_GET(self) -> None:
        self._record()
        route = _ROUTES.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        status, body = route
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self) -> None:
        self._record()
        self.send_response(204)
        self.end_headers()

    def do_DELETE(self) -> None:
        self._record()
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args: object) -> None:  # silence the test server
        pass


@pytest.fixture
def blob(sock_dir: Path) -> Iterator[BlobClient]:
    _ROUTES.clear()
    _RECORDED.clear()
    sock = str(sock_dir / "s")
    server = UnixStreamServer(sock, _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ["FUNCD_INVOKE_SOCKET"] = sock
    try:
        yield BlobClient()
    finally:
        os.environ.pop("FUNCD_INVOKE_SOCKET", None)
        server.shutdown()
        server.server_close()


def test_scenario_blob_read_write(blob: BlobClient) -> None:
    _ROUTES["/blob/files/report.txt"] = (200, b"hello")
    blob.put("files", "report.txt", b"hello")
    assert _RECORDED[0] == ("PUT", "/blob/files/report.txt", b"hello")
    assert blob.get("files", "report.txt") == b"hello"


def test_scenario_blob_missing_is_none(blob: BlobClient) -> None:
    assert blob.get("files", "absent") is None


def test_scenario_blob_list(blob: BlobClient) -> None:
    _ROUTES["/blob/files?prefix=bronze%2F"] = (200, b'["bronze/a","bronze/b"]')
    assert blob.list("files", "bronze/") == ["bronze/a", "bronze/b"]


def test_scenario_blob_signed_url(blob: BlobClient) -> None:
    _ROUTES["/blob/files/report.txt?sign=1&method=PUT&expiry=10m"] = (
        200,
        b"https://signed.example/p/report.txt",
    )
    url = blob.signed_url("files", "report.txt", method="PUT", expiry="10m")
    assert url == "https://signed.example/p/report.txt"


def test_scenario_blob_signed_url_none_omits_expiry(blob: BlobClient) -> None:
    _ROUTES["/blob/files/report.txt?sign=1&method=GET"] = (200, b"https://signed.example/g")
    assert blob.signed_url("files", "report.txt") == "https://signed.example/g"
    assert _RECORDED[0][1] == "/blob/files/report.txt?sign=1&method=GET"


def test_scenario_blob_signed_url_sends_expiry_as_given(blob: BlobClient) -> None:
    # The server owns the grammar (ADR-0198): the shim sends an empty or odd value and surfaces the 400.
    _ROUTES["/blob/files/k?sign=1&method=GET&expiry="] = (400, b"invalid expiry")
    with pytest.raises(RuntimeError, match=r"context\.blob\.signed_url failed: 400"):
        blob.signed_url("files", "k", expiry="")
    _ROUTES["/blob/files/k?sign=1&method=GET&expiry=1h%2030m"] = (400, b"invalid expiry")
    with pytest.raises(RuntimeError, match=r"context\.blob\.signed_url failed: 400"):
        blob.signed_url("files", "k", expiry="1h 30m")
    assert [r[1] for r in _RECORDED] == [
        "/blob/files/k?sign=1&method=GET&expiry=",
        "/blob/files/k?sign=1&method=GET&expiry=1h%2030m",
    ]


@pytest.mark.parametrize("expiry", [60, 60.0, b"10m"])
def test_scenario_blob_signed_url_non_string_expiry_raises(blob: BlobClient, expiry: object) -> None:
    with pytest.raises(TypeError, match="expiry must be a duration string such as 10m or 1h30m"):
        blob.signed_url("files", "k", expiry=expiry)  # type: ignore[arg-type]
    assert _RECORDED == []


def test_scenario_blob_error_raises(blob: BlobClient) -> None:
    _ROUTES["/blob/nope/k"] = (403, b"forbidden")
    with pytest.raises(RuntimeError, match=r"context\.blob\.get failed: 403"):
        blob.get("nope", "k")
