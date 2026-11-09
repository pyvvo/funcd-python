"""Scenario tests for context.kv's conditional writes and expiring keys over a fake worker-node local API: the
headers each call sends, and how it reads 201/204/412 and the ETag."""

import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import UnixStreamServer

import pytest

from funcd_shim.kv import KVClient, KVEntry

# Each request the fake saw (method, path, headers), and the replies it gives in order: (status, etag, body).
_SEEN: list[tuple[str, str, dict[str, str]]] = []
_REPLIES: list[tuple[int, str | None, bytes]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _answer(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        _SEEN.append((self.command, self.path, {k: v for k, v in self.headers.items()}))
        status, etag, body = _REPLIES.pop(0)
        self.send_response(status)
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._answer()

    def do_PUT(self) -> None:
        self._answer()

    def do_DELETE(self) -> None:
        self._answer()

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def kv(sock_dir: Path) -> Iterator[KVClient]:
    _SEEN.clear()
    _REPLIES.clear()
    sock = str(sock_dir / "s")
    server = UnixStreamServer(sock, _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ["FUNCD_INVOKE_SOCKET"] = sock
    try:
        yield KVClient()
    finally:
        os.environ.pop("FUNCD_INVOKE_SOCKET", None)
        server.shutdown()
        server.server_close()


def test_scenario_put_if_absent_reports_who_won(kv: KVClient) -> None:
    _REPLIES.extend([(201, '"7"', b""), (412, '"7"', b"held")])
    assert kv.put_if_absent("claims", "run/1", "me", ttl=30) == "7"
    assert kv.put_if_absent("claims", "run/1", "you") is None
    method, path, headers = _SEEN[0]
    assert (method, path, headers["If-None-Match"]) == ("PUT", "/kv/claims/run/1?ttl=30", "*")
    assert _SEEN[1][1] == "/kv/claims/run/1"


def test_scenario_compare_and_set_names_the_version(kv: KVClient) -> None:
    _REPLIES.extend([(204, '"9"', b""), (412, '"9"', b"moved")])
    assert kv.compare_and_set("claims", "run/1", "me", "7", ttl=30) == "9"
    assert kv.compare_and_set("claims", "run/1", "me", "7") is None
    assert _SEEN[0][2]["If-Match"] == '"7"'


def test_scenario_delete_if_releases_only_what_it_holds(kv: KVClient) -> None:
    _REPLIES.extend([(412, '"9"', b""), (204, None, b"")])
    assert kv.delete_if("claims", "run/1", "7") is False
    assert kv.delete_if("claims", "run/1", "9") is True
    assert [(m, h["If-Match"]) for m, _, h in _SEEN] == [("DELETE", '"7"'), ("DELETE", '"9"')]


def test_scenario_get_entry_has_the_version(kv: KVClient) -> None:
    _REPLIES.extend([(200, '"9"', b"me"), (404, None, b"")])
    assert kv.get_entry("claims", "run/1") == KVEntry(b"me", "9")
    assert kv.get_entry("claims", "run/2") is None


def test_put_with_ttl_and_plain_calls_send_no_condition(kv: KVClient) -> None:
    _REPLIES.extend([(204, '"3"', b""), (204, None, b""), (200, '"3"', b"x")])
    kv.put("cache", "k", "x", ttl=60)
    kv.delete("cache", "k")
    assert kv.get("cache", "k") == b"x"
    assert _SEEN[0][1] == "/kv/cache/k?ttl=60"
    for _, _, headers in _SEEN:
        assert "If-Match" not in headers and "If-None-Match" not in headers


@pytest.mark.parametrize("ttl", [0, -1, 1.5, True])
def test_a_bad_ttl_sends_nothing(kv: KVClient, ttl: object) -> None:
    with pytest.raises(ValueError):
        kv.put_if_absent("claims", "k", "x", ttl=ttl)  # type: ignore[arg-type]
    assert _SEEN == []


def test_an_unexpected_status_raises(kv: KVClient) -> None:
    _REPLIES.extend([(403, None, b"denied"), (412, None, b""), (404, None, b"no route")])
    with pytest.raises(RuntimeError, match="context.kv.put_if_absent failed: 403"):
        kv.put_if_absent("claims", "k", "x")
    with pytest.raises(RuntimeError, match="context.kv.delete failed: 412"):
        kv.delete("claims", "k")
    with pytest.raises(RuntimeError, match="context.kv.compare_and_set failed: 404"):
        kv.compare_and_set("claims", "k", "x", "1")
