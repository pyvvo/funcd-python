"""A request handler class served in-process on loopback, for the tests that look at its connections."""

import http.client
import re
import socket
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _RecordingServer(ThreadingHTTPServer):
    """Keeps each socket it accepts, so a test can read the options its handler set on the connection."""

    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(("127.0.0.1", 0), handler)
        self.accepted: list[socket.socket] = []

    def get_request(self) -> tuple[socket.socket, Any]:
        request, address = super().get_request()
        self.accepted.append(request)
        return request, address


def warm_calls(
    handler: type[BaseHTTPRequestHandler], method: str, path: str, body: bytes | None = None, calls: int = 40
) -> tuple[float, int]:
    """Send *calls* requests to *handler* on one kept-alive connection.

    Returns the median latency in milliseconds of the second half of the calls, and the TCP_NODELAY option
    the handler left on the connection. Linux acknowledges the first segments of a connection at once, so
    only the later calls show a wait for a delayed ACK."""
    server = _RecordingServer(handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        latencies: list[float] = []
        for _ in range(calls):
            start = time.perf_counter()
            conn.request(method, path, body)
            conn.getresponse().read()
            latencies.append((time.perf_counter() - start) * 1000)
        nodelay = server.accepted[0].getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return statistics.median(latencies[calls // 2 :]), nodelay


def exchange(address: tuple[str, int], payload: bytes, half_close: bool = True) -> bytes:
    """Send raw *payload* and return all the server wrote until it closed; with *half_close*, end the sending
    side first, so a server still waiting for bytes sees the end of the input."""
    with socket.create_connection(address, timeout=5) as sock:
        sock.sendall(payload)
        if half_close:
            sock.shutdown(socket.SHUT_WR)
        answers = b""
        while chunk := sock.recv(65536):
            answers += chunk
    return answers


def statuses(answers: bytes) -> list[int]:
    """The status of each answer in *answers*."""
    return [int(status) for status in re.findall(rb"HTTP/1\.1 (\d{3}) ", answers)]
