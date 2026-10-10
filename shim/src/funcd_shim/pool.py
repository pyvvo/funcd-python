"""funcd Python worker-pool host (ADR-0050) — N handlers in one process, each in its own
subinterpreter (one ``concurrent.futures.InterpreterPoolExecutor`` per handler, ``max_workers=1``,
Python ≥ 3.14): per-interpreter GIL + module-state isolation, the Python analog of the Node
``worker_threads`` pool (ADR-0044). The executor owns the interpreter+thread lifecycle and a Future
correlates each request↔response, so there is no hand-rolled queue/dispatch.

Reads ``FUNCD_POOL_MANIFEST`` (the SAME ``[{name, artifact, handler}]`` contract as ``pool.mjs``),
serves ``POST /function/<name>`` by submitting the request to the named handler's interpreter — with
the byte-identical wire contract + JSON Schema I/O validation (ADR-0058, ADR-0123) as the solo shim
(ADR-0049) — plus ``GET /health/{readiness,liveness,members}``; each ``/health/members`` entry of a ready
member carries funcd's dependency report when its check fails (funcd ADR-0215). Bind: ``FUNCD_PORT`` →
``0.0.0.0:PORT`` (container) else ``FUNCD_PORTFILE`` → loopback + write the port (process).

The host serves at once and loads every member concurrently, each bounded by
``FUNCD_POOL_LOAD_TIMEOUT_MS``: a member whose handler/contract fails to load, or does not load in
time, is ``failed`` (calls get 503) while its siblings serve. A manifest row's ``env`` reaches only
that member. Runtime dependency: fastjsonschema (the validators, ADR-0071).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from concurrent.futures import InterpreterPoolExecutor  # type: ignore[attr-defined]  # 3.14, no stubs yet
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from funcd_shim import _poolworker
from funcd_shim.funclog import new_write_lock
from funcd_shim.shim import check_dependencies, read_body
from funcd_shim.tracespan import parse_links

# funcd's runtime.bootTimeout default (1 m), used when FUNCD_POOL_LOAD_TIMEOUT_MS is unset or invalid.
_DEFAULT_LOAD_TIMEOUT_MS = 60_000


def load_timeout_s(value: str | None) -> float:
    """``FUNCD_POOL_LOAD_TIMEOUT_MS`` in seconds: a positive decimal integer, else the default."""
    ms = int(value) if value and value.isascii() and value.isdigit() else 0
    return (ms if ms >= 1 else _DEFAULT_LOAD_TIMEOUT_MS) / 1000


class _Pooled:
    """One pooled handler: a dedicated single-worker interpreter executor. ``max_workers=1`` keeps
    one in-flight request per handler (its interpreter is single-threaded); different handlers run in
    parallel via their own interpreters (per-GIL). Its state is ``loading``, then ``ready`` or
    ``failed`` (a first load that failed or timed out; not retried in this process)."""

    def __init__(
        self,
        src: str,
        artifact: str,
        handler: str,
        contract: str | None = None,
        write_lock: tuple[int, int] | None = None,
        name: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        # ADR-0123: contract is the delivered contract-blob path (from the manifest's "contract"
        # field); the worker compiles its validator from it at init, ahead of the handler import.
        self.name = name
        self.state = "loading"
        self.error: str | None = None
        self.ex = InterpreterPoolExecutor(
            max_workers=1,
            initializer=_poolworker.init,
            initargs=(src, artifact, handler, contract, write_lock, name, env),
        )

    def load(self, timeout_s: float) -> None:
        """Run the initializer and settle the state. A load past *timeout_s* leaves its thread behind:
        an import cannot be interrupted."""
        future = self.ex.submit(_poolworker.load_error)
        try:
            error = future.result(timeout=timeout_s)
        except TimeoutError:
            error = "load timed out"
            self.ex.shutdown(wait=False, cancel_futures=True)
        except Exception as err:  # noqa: BLE001 - a broken interpreter fails this member alone
            error = f"{type(err).__name__}: {err}"
        if error is None:
            self.state = "ready"
            return
        print(f"funcd-pool: {self.name!r} failed to load: {error}", file=sys.stderr, flush=True)
        self.error = error
        self.state = "failed"

    def status(self) -> dict[str, str]:
        out = {"name": self.name or "", "state": self.state}
        if self.error is not None:
            out["error"] = self.error
        return out

    def invoke(
        self,
        body: bytes,
        traceparent: str | None = None,
        fn_name: str = "invoke",
        span_id: str | None = None,
        links: list[str] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = self.ex.submit(
            _poolworker.invoke, body, traceparent, fn_name, span_id, links
        ).result()
        return result

    def close(self) -> None:
        self.ex.shutdown(wait=False)


def _src_dir() -> str:
    """The directory holding the funcd_shim package, so each worker interpreter can import it."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_request_handler(handlers: dict[str, _Pooled]) -> type[BaseHTTPRequestHandler]:
    class PoolHandler(BaseHTTPRequestHandler):
        # HTTP/1.1 → keep-alive (see shim.py): reuse the TCP connection instead of closing per
        # request — avoids a handshake + TIME_WAIT per call and lets the upstream pool (ADR-0041)
        # reuse connections. Safe: every response carries Content-Length.
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return

        def _json(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _text(self, status: int, text: str) -> None:
            payload = text.encode()
            self.send_response(status)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("content-length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - stdlib signature
            if self.path == "/health/liveness":
                self._text(200, "ok")
            elif self.path == "/health/readiness":
                if any(p.state == "loading" for p in handlers.values()):
                    self._text(503, "not ready")
                else:
                    self._text(200, "ready")
            elif self.path == "/health/members":
                deps = check_dependencies(list(handlers))
                rows: list[dict[str, Any]] = []
                for name, pooled in handlers.items():
                    row: dict[str, Any] = dict(pooled.status())
                    if name in deps:
                        row["dependency"] = json.loads(deps[name])
                    rows.append(row)
                self._json(200, json.dumps(rows).encode())
            else:
                self._empty(404)

        def do_POST(self) -> None:  # noqa: N802 - stdlib signature
            # Drain the request body FIRST, before any early return — with HTTP/1.1 keep-alive an
            # unread body would desync the next request on the connection.
            raw = read_body(self)
            if raw is None:
                return
            if not self.path.startswith("/function/"):
                self._empty(404)
                return
            name = self.path[len("/function/") :]
            pooled = handlers.get(name)
            if pooled is None:
                self._empty(404)
                return
            if pooled.state != "ready":
                self._json(503, json.dumps({"error": f"function {name} unavailable"}).encode())
                return
            # ADR-0101: forward the trace header + function name so the worker's span adopts/names.
            # ADR-0101/0105: forward the trace + span-id + fan-in links headers to the worker.
            res = pooled.invoke(
                raw,
                self.headers.get("traceparent"),
                name,
                self.headers.get("X-Funcd-Span-Id"),
                parse_links(self.headers.get("X-Funcd-Span-Links")),
            )
            status = int(res["status"])
            if status == 204:
                self._empty(204)
            elif status == 400:
                self._text(400, res["text"])
            else:
                self._json(status, res["body"])

    return PoolHandler


def main() -> int:
    manifest_path = os.environ.get("FUNCD_POOL_MANIFEST")
    fixed_port = int(os.environ["FUNCD_PORT"]) if os.environ.get("FUNCD_PORT") else 0
    port_file = os.environ.get("FUNCD_PORTFILE")
    if not manifest_path:
        print("funcd-pool: FUNCD_POOL_MANIFEST is required", file=sys.stderr)
        return 2

    src = _src_dir()
    # Worker interpreters start with a fresh sys.path; PYTHONPATH carries the package dir into them so
    # the executor's referenced worker functions (funcd_shim._poolworker) import (init() also re-adds it).
    os.environ["PYTHONPATH"] = src + os.pathsep + os.environ.get("PYTHONPATH", "")

    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    write_lock = new_write_lock()
    handlers: dict[str, _Pooled] = {}
    for entry in manifest:
        handlers[entry["name"]] = _Pooled(
            src,
            entry["artifact"],
            entry["handler"],
            entry.get("contract"),
            write_lock,
            entry["name"],
            entry.get("env"),
        )
    timeout_s = load_timeout_s(os.environ.get("FUNCD_POOL_LOAD_TIMEOUT_MS"))
    for pooled in handlers.values():
        threading.Thread(target=pooled.load, args=(timeout_s,), daemon=True).start()

    hostname = "0.0.0.0" if fixed_port > 0 else "127.0.0.1"  # noqa: S104 - container bind is intentional
    server = ThreadingHTTPServer((hostname, fixed_port), make_request_handler(handlers))
    bound = server.server_address[1]
    if port_file:
        with open(port_file, "w", encoding="utf-8") as fh:
            fh.write(str(bound))
    print(f"funcd-pool: {len(handlers)} handlers listening on {hostname}:{bound}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for pooled in handlers.values():
            pooled.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
