"""Fixtures shared by the shim tests."""

import shutil
import socketserver
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

HOLD = "hold"
CLOSE = "close"


@pytest.fixture
def sock_dir() -> Iterator[Path]:
    """A short directory to bind Unix sockets in, independent of TMPDIR: an AF_UNIX path is limited to
    104 bytes on macOS (108 on Linux), and pytest's tmp_path under a long TMPDIR overruns it."""
    path = Path(tempfile.mkdtemp(prefix="fs", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class DependencyAPI:
    """A fake worker-node local API serving funcd's ``GET /health/dependencies`` (funcd ADR-0215).

    ``replies`` maps the ``X-Funcd-Member`` value (``"-"`` for none) to a ``(status, body)``, to ``HOLD``
    (no answer until the test ends) or to ``CLOSE`` (the connection closes without an answer); a caller
    with no entry gets 200. ``calls`` records ``"<method> <path> <member>"`` per request."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.replies: dict[str, tuple[int, bytes] | str] = {}
        self.calls: list[str] = []
        self.release = threading.Event()


@pytest.fixture
def dependency_api(sock_dir: Path) -> Iterator[DependencyAPI]:
    api = DependencyAPI(str(sock_dir / "api.sock"))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002 - stdlib signature
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib signature
            member = self.headers.get("X-Funcd-Member") or "-"
            api.calls.append(f"{self.command} {self.path} {member}")
            reply = api.replies.get(member, (200, b""))
            if reply == HOLD:
                api.release.wait(10)
            if isinstance(reply, str):
                self.close_connection = True
                return
            status, body = reply
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = _UnixHTTPServer(api.path, Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield api
    finally:
        api.release.set()
        server.shutdown()
        server.server_close()
