"""The funcd Python runtime shim (stdlib ``http.server``).

Loads the function artifact, resolves ``handle(context, event)`` (the materialization shape-gate),
and serves the runtime-shim HTTP contract — byte-for-byte identical to the Node shim (ADR-0030 §1,
ADR-0037), so the platform stays language-blind:

    POST /                 CloudEvent -> [input contract] -> handler -> [output contract] -> response
                           (dict/list->200 JSON, None/void->204, raise/output-mismatch->500 {error},
                            bad JSON->400, input-mismatch->422 {error, details})
    GET  /health/readiness once the handler resolved, funcd's dependency check (funcd ADR-0215):
                           200 when it passes, else 503 {kind, binding, reason, message}
    GET  /health/liveness  200 while up; never calls funcd

If the artifact carries a precompiled validator (ADR-0058/0060 — generated at build from the
author's ``FuncInput``/``FuncOutput`` contract; supersedes the ADR-0038 JTD ``event_schema``),
``event.data`` is validated before the handler runs (mismatch -> 422) and the result after
(mismatch -> 500); a void output contract -> 204 on an empty result, 500 on a non-empty one. The
validator is pure-Python (fastjsonschema), so it behaves identically solo and in the pool.

Env: ``FUNCD_ARTIFACT`` (local .py path, required), ``FUNCD_HANDLER`` (export, default ``handle``);
``FUNCD_PORT`` (container: bind ``0.0.0.0:PORT``) else ``FUNCD_PORTFILE`` (process: bind
``127.0.0.1:0`` and write the chosen port). Exit 2 = missing artifact; exit 3 = shape-gate failure.

Runtime dependency: fastjsonschema (the baked validator imports it); pydantic runs only at build.
"""

from __future__ import annotations

import http.client
import io
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from . import contract, jsonwire, runtime
from .blob import BlobClient
from .funclog import install_log_capture, open_channel
from .invoke import MEMBER_HEADER, _UnixHTTPConnection
from .invoke import invoke as _invoke
from .kv import KVClient
from .tracespan import InvocationSpan, parse_links
from .types import CloudEvent, FunctionContext, Handler

if TYPE_CHECKING:
    from .funclog import _Channel


class _Context:
    """The FunctionContext passed to every handler invocation (ADR-0010 logging)."""

    def __init__(self, channel: _Channel | None = None) -> None:
        self._channel = channel  # ADR-0165: where context.invoke writes its CLIENT span

    def log(self, *args: object) -> None:
        print(*args, flush=True)

    def invoke(self, alias: str, payload: Any) -> Any:
        return _invoke(alias, payload, channel=self._channel)

    @property
    def kv(self) -> KVClient:
        return KVClient()

    @property
    def blob(self) -> BlobClient:
        return BlobClient()


DEPENDENCY_CHECK_BUDGET = 0.05
"""funcd's DependencyCheckBudget (ADR-0215): a pool host's one bound for asking every member."""

READINESS_CHECK_TIMEOUT = 0.1
"""funcd's readiness probeTimeout: past it funcd no longer reads the shim's answer."""


def socket_report(reason: str, message: str) -> dict[str, str]:
    return {"kind": "socket", "binding": "", "reason": reason, "message": message}


def check_dependencies(member: str | None, timeout: float) -> bytes | None:
    """Ask funcd's ``GET /health/dependencies`` over ``FUNCD_INVOKE_SOCKET`` (funcd ADR-0215 Decision 4).

    Returns None on a pass, else the JSON report readiness answers with 503. 200 passes, and so do 404
    and no socket: a funcd without the endpoint. A 503 report is relayed as funcd wrote it; any other
    answer or a socket error is kind ``socket``. In a pool, *member* names the member being checked."""
    socket_path = os.environ.get("FUNCD_INVOKE_SOCKET")
    if not socket_path:
        return None
    conn = _UnixHTTPConnection(socket_path, timeout)
    try:
        conn.request("GET", "/health/dependencies", headers={MEMBER_HEADER: member} if member else {})
        resp = conn.getresponse()
        body = resp.read()
    except TimeoutError:
        return jsonwire.encode(socket_report("Timeout", f"funcd did not answer within {timeout:g}s"))
    except (OSError, http.client.HTTPException) as err:
        return jsonwire.encode(socket_report("Unreachable", f"{type(err).__name__}: {err}"))
    finally:
        conn.close()
    if resp.status in (200, 404):
        return None
    if resp.status == 503:
        return body
    return jsonwire.encode(socket_report("Unreachable", f"GET /health/dependencies answered {resp.status}"))


def read_body(request: BaseHTTPRequestHandler) -> bytes | None:
    """Read the request body its Content-Length frames (none: empty). A Content-Length that is not one
    1*DIGIT value leaves the framing unrecoverable (RFC 9112 §6.3): answer 400 and close, as Node's HTTP
    parser does, and return None."""
    lengths = request.headers.get_all("content-length") or ["0"]
    length = lengths[0].strip(" \t")
    if len(lengths) > 1 or not (length.isascii() and length.isdigit()):
        request.send_error(400, "invalid Content-Length")
        return None
    return request.rfile.read(int(length))


def make_request_handler(
    handler: Handler,
    validators: runtime.Validators,
    channel: _Channel | None = None,
    fn_name: str = "invoke",
) -> type[BaseHTTPRequestHandler]:
    """Build the ``BaseHTTPRequestHandler`` class that serves the contract around *handler*.

    The optional *validators* gate the I/O (ADR-0058): ``input`` validates ``event.data`` before
    the handler (mismatch → 422, handler never called); ``output`` validates the result after
    (mismatch → 500, never emitted as 200). A void output contract → 204 on empty / 500 otherwise.

    ADR-0101: when *channel* is set, each invocation emits an auto SERVER span on it (adopting the
    incoming ``traceparent`` or minting a root); *fn_name* names the span.
    """
    context: FunctionContext = _Context(channel)

    class ShimHandler(BaseHTTPRequestHandler):
        # HTTP/1.1 → keep-alive: reuse the TCP connection across requests instead of closing after
        # each (the HTTP/1.0 default), which otherwise forces a handshake per request and piles up
        # TIME_WAIT under load. Safe here because every response carries Content-Length. This also
        # lets the gateway/activator upstream pool (ADR-0041) actually reuse connections to the shim.
        protocol_version = "HTTP/1.1"

        # Quieten the default stderr access log; the platform owns logging (ADR-0010).
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return

        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            self._send_encoded(status, jsonwire.encode(body))

        def _send_encoded(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_text(self, status: int, text: str) -> None:
            payload = text.encode()
            self.send_response(status)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("content-length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - stdlib signature
            if self.path == "/health/liveness":
                self._send_text(200, "ok")
            elif self.path == "/health/readiness":
                report = check_dependencies(None, READINESS_CHECK_TIMEOUT)
                if report is None:
                    self._send_text(200, "ready")
                else:
                    self._send_encoded(503, report)
            else:
                self._send_empty(404)

        def do_POST(self) -> None:  # noqa: N802 - stdlib signature
            # Drain the request body FIRST, before any early return — with HTTP/1.1 keep-alive an
            # unread body would desync the next request on the connection.
            raw = read_body(self)
            if raw is None:
                return
            if self.path != "/":
                self._send_empty(404)
                return
            try:
                event: CloudEvent[Any] = jsonwire.decode(raw) if raw else CloudEvent()
            except ValueError:
                self._send_text(400, "invalid CloudEvent JSON")
                return
            if not isinstance(event, dict):
                # Valid JSON that is not an object (null, an array, a scalar) is no CloudEvent envelope:
                # reject it as the Node shim does, before `event.get` raises outside any handler.
                self._send_text(400, "request body must be a JSON object (CloudEvent envelope)")
                return
            if validators.input is not None:
                errors = validators.input(event.get("data"))
                if errors:
                    # ADR-0101: input-mismatch short-circuits before the handler → no invocation, no span.
                    self._send_json(
                        422,
                        {"error": "event data does not match the input contract", "details": errors},
                    )
                    return
            # ADR-0101: a real invocation begins → its SERVER span (adopts traceparent or mints a root);
            # the handler runs inside the span's context so its logs correlate.
            # ADR-0105: a workflow step is dispatched with the span-id to USE + its fan-in links.
            with InvocationSpan(
                channel,
                fn_name,
                self.headers.get("traceparent"),
                self.headers.get("X-Funcd-Span-Id"),
                parse_links(self.headers.get("X-Funcd-Span-Links")),
            ) as span:
                try:
                    result = runtime.call_handler(handler, context, event)
                except BaseException as err:  # noqa: BLE001 - user handler errors, SystemExit too, become 500
                    span.fail(str(err))
                    self._send_json(500, {"error": str(err)})
                    return
                if validators.output is not None:
                    errors = validators.output(result)
                    if errors:
                        span.fail("handler result does not match the output contract")
                        self._send_json(
                            500,
                            {"error": "handler result does not match the output contract", "details": errors},
                        )
                        return
                if result is None:
                    self._send_empty(204)
                    return
                try:
                    payload = jsonwire.encode(result)
                except Exception as err:  # noqa: BLE001 - a result with no JSON form is a handler failure
                    span.fail(str(err))
                    self._send_json(500, {"error": str(err)})
                    return
                self._send_encoded(200, payload)

    return ShimHandler


def main(argv: list[str] | None = None) -> int:
    """Load the artifact, resolve the handler + optional contract, and serve. Returns the process
    exit code (0 only if the server is interrupted cleanly)."""
    # Path B log capture (ADR-0081) + traces (ADR-0101): open the telemetry channel ONCE and share it
    # between log capture and the per-invocation span (a second connect would double-capture). Install
    # BEFORE the handler loads so the first logging.* is captured. No-op unless FUNCD_LOG_FD/SOCK is set.
    # The shim's own messages use print(... stderr), not logging, so they are never captured.
    # ADR-0168: print() reaches funcd's stdout reader line by line, not when a block buffer fills.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    channel = open_channel()
    install_log_capture(channel)

    artifact = os.environ.get("FUNCD_ARTIFACT")
    handler_name = os.environ.get("FUNCD_HANDLER", "handle")
    fixed_port = int(os.environ["FUNCD_PORT"]) if os.environ.get("FUNCD_PORT") else 0
    port_file = os.environ.get("FUNCD_PORTFILE")

    if not artifact:
        print("funcd-shim: FUNCD_ARTIFACT is required", file=sys.stderr)
        return 2

    # ADR-0123: compile the delivered contract BEFORE importing the (untrusted) handler module — the
    # bounded eval-free reversal (the schema-compile runs over a contract.Check-gated, digest-pinned
    # schema, ahead of any handler code). FUNCD_CONTRACT_PATH set-but-broken → fail closed (exit 3).
    try:
        delivered = contract.load()
    except contract.ContractError as err:
        print(f"funcd-shim: contract error: {err}", file=sys.stderr)
        return 3
    try:
        module = runtime.load_module(artifact)
        handler = runtime.resolve_handler(module, handler_name)
    except runtime.ShapeError as err:
        print(f"funcd-shim: shape error: {err}", file=sys.stderr)
        return 3  # materialization shape-gate failure (ADR-0030 §3)
    # The delivered schema is authoritative when present; else fall back to the module-baked
    # validators (transition back-compat for artifacts still carrying __funcd_validate_*).
    validators = delivered if delivered is not None else runtime.resolve_validators(module)

    hostname = "0.0.0.0" if fixed_port > 0 else "127.0.0.1"  # noqa: S104 - container bind is intentional
    fn_name = os.environ.get("FUNCD_FUNCTION", "invoke")
    server = ThreadingHTTPServer(
        (hostname, fixed_port), make_request_handler(handler, validators, channel, fn_name)
    )
    bound_port = server.server_address[1]
    if port_file:
        with open(port_file, "w", encoding="utf-8") as fh:
            fh.write(str(bound_port))
    print(f"funcd-shim: listening on {hostname}:{bound_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
