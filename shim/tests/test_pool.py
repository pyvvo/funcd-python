"""Tests for the subinterpreter pool host (ADR-0050). Gated on Python ≥ 3.14 — they
``importorskip`` ``concurrent.interpreters`` (PEP 734), exactly as the node pool tests are
node-gated. Run with ``uv run --python 3.14 pytest``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import fastjsonschema
import pytest

pytest.importorskip("concurrent.interpreters")  # Python 3.14+ only

SRC = str(Path(__file__).resolve().parents[1] / "src")

# A REAL fastjsonschema-compiled __funcd_validate_input (what the build bakes) — this proves
# fastjsonschema's compiled validator runs end-to-end inside the subinterpreter pool (the whole
# reason we chose it over pydantic-core, which crashes a subinterpreter).
_SCHEMA = {
    "type": "object",
    "properties": {"hello": {"type": "string"}},
    "required": ["hello"],
    "additionalProperties": False,
}
_VALIDATOR = fastjsonschema.compile_to_code(_SCHEMA)
ECHO = (
    _VALIDATOR + "\n"
    "def __funcd_validate_input(d):\n"
    "    try:\n"
    "        validate(d)\n"
    "        return []\n"
    "    except JsonSchemaValueException as e:\n"
    "        return [str(e)]\n"
    "def handle(context, event):\n"
    "    return {'echoed': event.get('data')}\n"
)
COUNTER = (
    "_count = 0\n"
    "def handle(context, event):\n"
    "    global _count\n"
    "    _count += 1\n"
    "    return {'count': _count}\n"
)
# Two CPU handlers meet on a FIFO (opening one end blocks until the other end is open). Then each spins,
# writing its counter to its own byte of a shared map and counting the changes it sees in its sibling's
# byte. Under one shared GIL a handler can see a change only after a GIL switch: a few per run.
CPU = (
    "import mmap\n"
    "import os\n"
    "def handle(context, event):\n"
    "    d = event['data']\n"
    "    me, other = d['me'], 1 - d['me']\n"
    "    with open(d['shared'], 'r+b') as f:\n"
    "        mm = mmap.mmap(f.fileno(), 2)\n"
    "    os.close(os.open(d['fifo'], os.O_WRONLY if me else os.O_RDONLY))\n"
    "    seen, last = 0, mm[other]\n"
    "    for i in range(1_000_000):\n"
    "        mm[me] = i & 255\n"
    "        cur = mm[other]\n"
    "        if cur != last:\n"
    "            seen += 1\n"
    "            last = cur\n"
    "    mm.close()\n"
    "    return {'seen': seen}\n"
)
LOGGER = (
    "import logging\n"
    "def handle(context, event):\n"
    "    for i in range(event['data']['lines']):\n"
    "        logging.info('%s %d %s', event['data']['tag'], i, 'x' * 16384)\n"
    "    return None\n"
)

FAILING = (
    "def handle(context, event):\n"
    "    kind = event['data']\n"
    "    if kind == 'set':\n"
    "        return {1}\n"
    "    if kind == 'bytes':\n"
    "        return b'x'\n"
    "    if kind == 'lambda':\n"
    "        return lambda: 0\n"
    "    if kind == 'nan':\n"
    "        return {'x': float('nan')}\n"
    "    if kind == 'exit':\n"
    "        raise SystemExit(3)\n"
    "    raise KeyboardInterrupt\n"
)

ASYNC = (
    "import asyncio\n"
    "async def handle(context, event):\n"
    "    await asyncio.sleep(0)\n"
    "    return {'echoed': event.get('data')}\n"
)


def _manifest(tmp: Path, members: list[tuple[str, str]]) -> Path:
    entries = []
    for i, (name, body) in enumerate(members):
        art = tmp / f"{name}_{i}.py"
        art.write_text(body)
        entries.append({"name": name, "artifact": str(art), "handler": "handle"})
    mpath = tmp / "manifest.json"
    mpath.write_text(json.dumps(entries))
    return mpath


def _start(
    tmp: Path, manifest: Path, env: dict[str, str] | None = None, pass_fds: tuple[int, ...] = ()
) -> tuple[subprocess.Popen[bytes], int]:
    port_file = tmp / "pool.port"
    proc = subprocess.Popen(
        [sys.executable, "-m", "funcd_shim.pool"],
        env={
            "FUNCD_POOL_MANIFEST": str(manifest),
            "FUNCD_PORTFILE": str(port_file),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": SRC,
            **(env or {}),
        },
        pass_fds=pass_fds,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"pool host exited early: {proc.returncode}")
        if port_file.exists() and port_file.read_text().strip():
            return proc, int(port_file.read_text().strip())
        time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("pool host never wrote its port")


def _post(port: int, name: str, body: str) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/function/{name}",
        data=body.encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_pool_colocates_and_contract(tmp_path: Path) -> None:
    # scenario: py-pool-colocates + py-pool-contract — two handlers in one host; same wire contract.
    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", ECHO), ("f1", ECHO)]))
    try:
        for name in ("f0", "f1"):
            st, body = _post(port, name, json.dumps({"data": {"hello": "world"}}))
            assert st == 200, body
            assert json.loads(body) == {"echoed": {"hello": "world"}}
        # input-contract mismatch → 422 (the per-handler pydantic validator, ADR-0058)
        st, body = _post(port, "f0", json.dumps({"data": {"hello": 5}}))
        assert st == 422
        assert json.loads(body)["error"] == "event data does not match the input contract"
        # health + unknown route
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/readiness", timeout=5) as r:  # noqa: S310
            assert r.status == 200
        assert _post(port, "nope", "{}")[0] == 404
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_issue_131_pool_answers_unencodable_results_and_base_exceptions(tmp_path: Path) -> None:
    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", FAILING)]))
    try:
        for kind in ("set", "bytes", "lambda", "exit", "kbint"):
            st, body = _post(port, "f0", json.dumps({"data": kind}))
            assert st == 500, (kind, body)
            assert json.loads(body)["error"] is not None
        st, body = _post(port, "f0", json.dumps({"data": "nan"}))
        assert st == 200
        assert json.loads(body) == {"x": None}
        assert _post(port, "f0", '{"data": NaN}')[0] == 400
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_issue_r21_pool_response_json_is_written_like_json_stringify(tmp_path: Path) -> None:
    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", ECHO)]))
    try:
        hello = '{"echoed":{"hello":"é"}}'.encode()
        assert _post(port, "f0", json.dumps({"data": {"hello": "é"}})) == (200, hello)
        mismatch = (
            b'{"error":"event data does not match the input contract",'
            b'"details":["data.hello must be string"]}'
        )
        assert _post(port, "f0", json.dumps({"data": {"hello": 5}})) == (422, mismatch)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_issue_r22_pool_400_answers_match_the_solo_shim(tmp_path: Path) -> None:
    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", ECHO)]))
    try:
        for body in ("null", "[1]", "42", '"s"', "true"):
            st, payload = _post(port, "f0", body)
            assert (st, payload) == (400, b"request body must be a JSON object (CloudEvent envelope)"), body
        assert _post(port, "f0", "abc{") == (400, b"invalid CloudEvent JSON")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_issue_188_pool_async_handler_is_awaited(tmp_path: Path) -> None:
    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", ASYNC)]))
    try:
        st, body = _post(port, "f0", json.dumps({"data": {"hello": "world"}}))
        assert st == 200, body
        assert json.loads(body) == {"echoed": {"hello": "world"}}
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_keep_alive_no_desync(tmp_path: Path) -> None:
    # HTTP/1.1 keep-alive on the pool host: requests on ONE connection, incl. a 404 POST with a body
    # (an unknown /function/<name>), must not desync — the body is drained before the early return.
    import http.client

    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", ECHO)]))
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/function/f0", b'{"data":{"hello":"a"}}')
        r = conn.getresponse()
        assert r.status == 200
        assert json.loads(r.read())["echoed"] == {"hello": "a"}

        conn.request("POST", "/function/nope", b'{"junk":true}')  # 404 WITH a body
        r = conn.getresponse()
        assert r.status == 404
        r.read()

        conn.request("POST", "/function/f0", b'{"data":{"hello":"b"}}')  # same conn still aligned
        r = conn.getresponse()
        assert r.status == 200
        assert json.loads(r.read())["echoed"] == {"hello": "b"}
        conn.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_isolates(tmp_path: Path) -> None:
    # scenario: py-pool-isolates — same artifact deployed twice; each handler has its OWN interpreter
    # state, so their counters are independent (a shared interpreter would share the module global).
    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", COUNTER), ("f1", COUNTER)]))
    try:
        for _ in range(3):
            _post(port, "f0", "{}")
        _, last_f0 = _post(port, "f0", "{}")
        _, first_f1 = _post(port, "f1", "{}")
        assert json.loads(last_f0)["count"] == 4
        assert json.loads(first_f1)["count"] == 1  # NOT 5 — separate interpreter state
    finally:
        proc.terminate()
        proc.wait(timeout=5)


MUTATOR = (
    "import os\n"
    "def handle(context, event):\n"
    "    rejected = []\n"
    "    for name, arg in (('chdir', '/'), ('umask', 0o077)):\n"
    "        try:\n"
    "            getattr(os, name)(arg)\n"
    "        except RuntimeError:\n"
    "            rejected.append(name)\n"
    "    return {'rejected': rejected}\n"
)
OBSERVER = (
    "import os\n"
    "def handle(context, event):\n"
    "    probe = os.path.join(event['data']['dir'], 'probe-' + event['data']['tag'])\n"
    "    os.close(os.open(probe, os.O_CREAT | os.O_WRONLY, 0o666))\n"
    "    return {'cwd': os.getcwd(), 'mode': os.stat(probe).st_mode & 0o777}\n"
)


def test_issue_183_chdir_umask_do_not_leak_to_siblings(tmp_path: Path) -> None:
    # The working directory and the umask belong to the process, not to a subinterpreter, so a
    # member that changes them would change them for every sibling. Like process.chdir/umask in a
    # Node worker (ADR-0044), the pool refuses them.
    proc, port = _start(tmp_path, _manifest(tmp_path, [("mutator", MUTATOR), ("observer", OBSERVER)]))
    try:

        def observe(tag: str) -> dict[str, object]:
            st, body = _post(port, "observer", json.dumps({"data": {"dir": str(tmp_path), "tag": tag}}))
            assert st == 200, body
            seen: dict[str, object] = json.loads(body)
            return seen

        before = observe("before")
        st, body = _post(port, "mutator", "{}")
        assert st == 200, body
        assert observe("after") == before
        assert json.loads(body)["rejected"] == ["chdir", "umask"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_issue_r27_pool_parallel_runs_handlers_at_the_same_time(tmp_path: Path) -> None:
    # scenario: py-pool-parallel — CPU-bound handlers run on per-interpreter GILs, so two of them run on
    # two cores at the same moments. Counting those moments, instead of comparing wall-clock times
    # against a baseline, keeps host load and core types out of the result.
    fifo = tmp_path / "start.fifo"
    os.mkfifo(fifo)
    shared = tmp_path / "shared"
    shared.write_bytes(b"\0\0")
    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", CPU), ("f1", CPU)]))
    try:
        replies: list[tuple[int, bytes]] = []

        def hit(me: int) -> None:
            data = {"fifo": str(fifo), "shared": str(shared), "me": me}
            replies.append(_post(port, f"f{me}", json.dumps({"data": data})))

        ts = [threading.Thread(target=hit, args=(me,)) for me in (0, 1)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert [st for st, _ in replies] == [200, 200], replies
        seen = sum(json.loads(body)["seen"] for _, body in replies)
        assert seen > 10_000, f"two concurrent CPU handlers saw each other run {seen} times: a shared GIL"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_issue_81_concurrent_pool_logs_keep_records_whole(tmp_path: Path) -> None:
    # pyvvo/funcd#81: every pool worker writes its records to the one FUNCD_LOG_FD pipe, and a write
    # longer than PIPE_BUF is not atomic, so two handlers logging at once must not splice their lines.
    lines = 300
    read_fd, write_fd = os.pipe()
    received: list[bytes] = []

    def drain() -> None:
        # Start late and read slowly, so both handlers block mid-line on a full pipe and race for space.
        time.sleep(0.2)
        while chunk := os.read(read_fd, 512):
            received.append(chunk)

    manifest = _manifest(tmp_path, [("f0", LOGGER), ("f1", LOGGER)])
    proc, port = _start(tmp_path, manifest, {"FUNCD_LOG_FD": str(write_fd)}, (write_fd,))
    os.close(write_fd)
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    statuses: list[int] = []

    def hit(name: str) -> None:
        statuses.append(_post(port, name, json.dumps({"data": {"tag": name, "lines": lines}}))[0])

    try:
        ts = [threading.Thread(target=hit, args=(n,)) for n in ("f0", "f1")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    reader.join(timeout=10)
    os.close(read_fd)

    assert statuses == [204, 204]
    tags: list[str] = []
    spliced = 0
    for raw in b"".join(received).splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            spliced += 1
            continue
        if rec.get("funcd.source") == "logging":
            tags.append(rec["body"].split(" ", 1)[0])
    assert spliced == 0, f"{spliced} unreadable lines on the shared log fd"
    assert tags.count("f0") == lines
    assert tags.count("f1") == lines


def test_pool_delivered_contract_enforces(tmp_path: Path) -> None:
    # scenario: runtime-compiles-validator (pool) — a member whose manifest carries a "contract"
    # path gets its validator COMPILED from that delivered schema at init (no baked symbol), and
    # enforces input→422 exactly like the solo shim.
    art = tmp_path / "schema_only.py"
    # a schema-only handler: NO __funcd_validate_* baked in — validation comes from the contract.
    art.write_text("def handle(context, event):\n    return {'echoed': event.get('data')}\n")
    cpath = tmp_path / "contract.json"
    cpath.write_text(
        json.dumps(
            {
                "input": {
                    "type": "object",
                    "properties": {"hello": {"type": "string"}},
                    "required": ["hello"],
                    "additionalProperties": False,
                },
                "output": {},
            }
        )
    )
    mpath = tmp_path / "m.json"
    mpath.write_text(
        json.dumps(
            [
                {"name": "f0", "artifact": str(art), "handler": "handle", "contract": str(cpath)},
            ]
        )
    )

    proc, port = _start(tmp_path, mpath)
    try:
        assert _post(port, "f0", json.dumps({"data": {"hello": "world"}}))[0] == 200
        st, body = _post(port, "f0", json.dumps({"data": {"hello": 5}}))
        assert st == 422, body
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_broken_contract_fails_closed(tmp_path: Path) -> None:
    # scenario: no-fail-open (pool) — a member with a set-but-missing contract path fails the whole
    # host closed (exit 3), never serving un-validated.
    art = tmp_path / "fn.py"
    art.write_text("def handle(context, event):\n    return None\n")
    mpath = tmp_path / "m.json"
    mpath.write_text(
        json.dumps(
            [
                {
                    "name": "f0",
                    "artifact": str(art),
                    "handler": "handle",
                    "contract": str(tmp_path / "absent.json"),
                },
            ]
        )
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "funcd_shim.pool"],
        env={
            "FUNCD_POOL_MANIFEST": str(mpath),
            "FUNCD_PORTFILE": str(tmp_path / "p"),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": SRC,
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert proc.wait(timeout=15) == 3


def test_pool_shape_gate(tmp_path: Path) -> None:
    # scenario: a member whose handler export is missing makes the whole host exit 3.
    bad = tmp_path / "bad.py"
    bad.write_text("x = 1\n")  # no handle
    entries = [{"name": "f0", "artifact": str(bad), "handler": "handle"}]
    mpath = tmp_path / "m.json"
    mpath.write_text(json.dumps(entries))
    proc = subprocess.Popen(
        [sys.executable, "-m", "funcd_shim.pool"],
        env={
            "FUNCD_POOL_MANIFEST": str(mpath),
            "FUNCD_PORTFILE": str(tmp_path / "p"),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": SRC,
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert proc.wait(timeout=15) == 3
