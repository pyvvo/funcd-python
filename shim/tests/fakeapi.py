"""Fakes of funcd's worker-node local API, served on a Unix socket for the shim tests."""

import socketserver
import threading

HOLD = "hold"
CLOSE = "close"


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
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
