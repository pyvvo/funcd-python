"""Tests for context.sql (funcd ADR-0233) over a fake worker-node local API: the wire form of statements and
parameters, rows as dicts or values, a batch, RETURNING rows, the pool member header and a refused call."""

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import UnixStreamServer
from typing import Any

import pytest

from funcd_shim.sql import SQLClient

# Each request as (path, member header, content type, body); the next reply as (status, body).
_CALLS: list[tuple[str, str | None, str | None, Any]] = []
_REPLY: list[tuple[int, bytes]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _CALLS.append((self.path, self.headers.get("X-Funcd-Member"), self.headers.get("Content-Type"), body))
        status, reply = _REPLY[0]
        self.send_response(status)
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *_args: object) -> None:
        pass


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


_JOIN = b'{"results":[{"columns":["id","id","title"],"values":[[1,7,"milk"],[2,8,"eggs"]]}]}'


def test_scenario_bound_function_reads(api: None) -> None:
    _REPLY.append((200, _JOIN))
    sql = SQLClient()
    assert sql.query("app", "SELECT … WHERE done = ?", [0]) == [
        {"id": 7, "title": "milk"},
        {"id": 8, "title": "eggs"},
    ]
    assert sql.query_values("app", "SELECT …")["columns"] == ["id", "id", "title"]
    assert _CALLS == [
        ("/sql/app/query", None, "application/json", {"statements": [["SELECT … WHERE done = ?", 0]]}),
        ("/sql/app/query", None, "application/json", {"statements": [["SELECT …"]]}),
    ]


def test_scenario_named_parameters(api: None) -> None:
    _REPLY.append((200, b'{"results":[{"columns":["id"],"values":[]}]}'))
    assert SQLClient().query("app", "SELECT id FROM todos WHERE done = :done", {"done": 0}) == []
    assert _CALLS[0][3] == {"statements": [["SELECT id FROM todos WHERE done = :done", {"done": 0}]]}


def test_scenario_owner_writes(api: None) -> None:
    sql = SQLClient("todo-api")
    _REPLY.append((200, b'{"results":[{"rowsAffected":1,"lastInsertId":3}]}'))
    assert sql.execute("app", "INSERT INTO todos(title, done) VALUES(?, ?)", ["milk", 0]) == {
        "rowsAffected": 1,
        "lastInsertId": 3,
    }
    _REPLY[0] = (
        200,
        b'{"results":[{"rowsAffected":1,"lastInsertId":4},{"rowsAffected":0,"lastInsertId":0,"columns":["id"],"values":[[4]]}]}',
    )
    batch = sql.execute("app", [("INSERT INTO todos(title, done) VALUES(:t, 0)", {"t": "eggs"}), "SELECT 1"])
    assert batch[1]["values"] == [[4]]
    assert _CALLS[1] == (
        "/sql/app/execute",
        "todo-api",
        "application/json",
        {"statements": [["INSERT INTO todos(title, done) VALUES(:t, 0)", {"t": "eggs"}], ["SELECT 1"]]},
    )


def test_scenario_non_writer_write_refused(api: None) -> None:
    _REPLY.append((403, b'{"title":"Forbidden"}'))
    with pytest.raises(RuntimeError, match=r'context\.sql\.execute failed: 403 \{"title":"Forbidden"\}'):
        SQLClient().execute("app", "DELETE FROM todos")


def test_socket_unset() -> None:
    os.environ.pop("FUNCD_INVOKE_SOCKET", None)
    with pytest.raises(RuntimeError, match="FUNCD_INVOKE_SOCKET unset"):
        SQLClient().query("app", "SELECT 1")
