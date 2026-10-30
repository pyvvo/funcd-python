"""Tests for the subinterpreter pool host (ADR-0050). Gated on Python ≥ 3.14 — they
``importorskip`` ``concurrent.interpreters`` (PEP 734), exactly as the node pool tests are
node-gated. Run with ``uv run --python 3.14 pytest``."""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest
from fakeapi import HOLD, DependencyAPI, UnixHTTPServer
from loopback import warm_calls

from funcd_shim import deadline
from funcd_shim.pool import _Pooled, load_timeout_s, make_request_handler

pytest.importorskip("concurrent.interpreters")  # Python 3.14+ only

SRC = str(Path(__file__).resolve().parents[1] / "src")
TIMEOUT = "X-Funcd-Timeout-Ms"
# The pool host's entry script (embed.go) after setting delays of the host's funcd_shim.deadline; a
# member's subinterpreter imports its own copy, with the module's values.
PATCHED_ENTRY = (
    "from funcd_shim import deadline\n{patch}from funcd_shim.pool import main\nraise SystemExit(main())\n"
)

ECHO = "def handle(context, event):\n    return {'echoed': event.get('data')}\n"
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
    tmp: Path,
    manifest: Path,
    env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
    settle: bool = True,
    constants: dict[str, float] | None = None,
    pid1: bool = False,
) -> tuple[subprocess.Popen[bytes], int]:
    """Start the pool host, with the delays of its funcd_shim.deadline set to *constants* and, with
    *pid1*, as if it were PID 1; with *settle*, return once no member is loading (the host serves at
    once)."""
    port_file = tmp / "pool.port"
    launch = [sys.executable, "-m", "funcd_shim.pool"]
    if constants or pid1:
        patch = "".join(f"deadline.{name} = {value!r}\n" for name, value in (constants or {}).items())
        patch += "import os\nos.getpid = lambda: 1\n" if pid1 else ""
        launch = [sys.executable, "-c", PATCHED_ENTRY.format(patch=patch)]
    proc = subprocess.Popen(  # noqa: S603 - this interpreter, on the test's own launcher
        launch,
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
            port = int(port_file.read_text().strip())
            if settle:
                _settled(port)
            return proc, port
        time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("pool host never wrote its port")


def _post(
    port: int, name: str, body: str | bytes, headers: dict[str, str] | None = None
) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/function/{name}",
        data=body if isinstance(body, bytes) else body.encode(),
        headers={"content-type": "application/json", **(headers or {})},
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


def test_issue_r38_pool_answers_a_non_utf8_body_with_400(tmp_path: Path) -> None:
    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", ECHO)]))
    try:
        assert _post(port, "f0", b'{"data":"\xff"}') == (400, b"invalid CloudEvent JSON")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_issue_r50_pool_malformed_content_length_returns_400(tmp_path: Path) -> None:
    proc, port = _start(tmp_path, _manifest(tmp_path, [("f0", ECHO)]))
    try:
        for value in ("abc", "-1"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.putrequest("POST", "/function/f0")
                conn.putheader("Content-Length", value)
                conn.endheaders()
                r = conn.getresponse()
                r.read()
                assert (r.status, r.getheader("connection")) == (400, "close"), value
            finally:
                conn.close()
        assert _post(port, "f0", json.dumps({"data": {"hello": "x"}}))[0] == 200
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


def test_issue_1337_pool_warm_calls_do_not_wait_for_a_delayed_ack() -> None:
    # The pool host answers in two writes too: see test_issue_1337_warm_calls_do_not_wait_for_a_delayed_ack.
    median_ms, nodelay = warm_calls(make_request_handler({}), "GET", "/health/liveness")
    assert median_ms < 20
    assert nodelay


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


LOCALE_MUTATOR = (
    "import _locale\n"
    "import locale\n"
    "def handle(context, event):\n"
    "    refused = []\n"
    "    for setlocale in (locale.setlocale, _locale.setlocale):\n"
    "        for name in ('C.UTF-8', 'en_US.UTF-8'):\n"
    "            try:\n"
    "                setlocale(locale.LC_NUMERIC, name)\n"
    "            except locale.Error as err:\n"
    "                refused.append(str(err))\n"
    "    return {'refused': refused, 'encoding': locale.getpreferredencoding()}\n"
)
LOCALE_OBSERVER = (
    "import locale\n"
    "def handle(context, event):\n"
    "    return {'numeric': locale.setlocale(locale.LC_NUMERIC)}\n"
)


def test_issue_r30_setlocale_does_not_leak_to_siblings(tmp_path: Path) -> None:
    # The C locale belongs to the process, so the pool refuses a change as setlocale's own
    # locale.Error. getpreferredencoding() still works: its restore of the current locale is a no-op.
    # UTF-8 mode is off, as in a runtime image with LANG=C.UTF-8, or getpreferredencoding() would
    # return before it saves and restores the locale.
    members = [("mutator", LOCALE_MUTATOR), ("observer", LOCALE_OBSERVER)]
    env = {"LANG": "C.UTF-8", "PYTHONUTF8": "0"}
    proc, port = _start(tmp_path, _manifest(tmp_path, members), env=env)
    try:
        before = _post(port, "observer", "{}")
        st, body = _post(port, "mutator", "{}")
        assert st == 200, body
        assert _post(port, "observer", "{}") == before
        out = json.loads(body)
        assert out["encoding"] == "UTF-8", out
        assert len(out["refused"]) == 4, out
        assert all("pooled handler" in err for err in out["refused"]), out
    finally:
        proc.terminate()
        proc.wait(timeout=5)


ENV_WRITER = (
    "import os\n"
    "os.environ['FUNCD_PROBE_LEAK'] = 'from-writer'\n"
    "del os.environ['FUNCD_PROBE_KEEP']\n"
    "def handle(context, event):\n"
    "    refused = []\n"
    "    for name, args in (('putenv', ('FUNCD_PROBE_LEAK', 'x')), ('unsetenv', ('FUNCD_PROBE_KEEP',))):\n"
    "        try:\n"
    "            getattr(os, name)(*args)\n"
    "        except RuntimeError:\n"
    "            refused.append(name)\n"
    "    leak, keep = os.environ.get('FUNCD_PROBE_LEAK'), os.environ.get('FUNCD_PROBE_KEEP')\n"
    "    return {'leak': leak, 'keep': keep, 'refused': refused}\n"
)
ENV_READER = (
    "import ctypes\n"
    "import os\n"
    "_getenv = ctypes.CDLL(None).getenv\n"
    "_getenv.restype = ctypes.c_char_p\n"
    "def handle(context, event):\n"
    "    seen = {}\n"
    "    for name in ('FUNCD_PROBE_LEAK', 'FUNCD_PROBE_KEEP'):\n"
    "        raw = _getenv(name.encode())\n"
    "        seen[name] = [os.environ.get(name), raw.decode() if raw else None]\n"
    "    return seen\n"
)


def test_issue_r31_environ_writes_stay_in_the_member(tmp_path: Path) -> None:
    # A member's os.environ is its own copy, as process.env is in a Node worker (ADR-0044/0050): a
    # write must not reach the process environment, which members started later copy and C getenv reads.
    members = [("before", ENV_READER), ("writer", ENV_WRITER), ("after", ENV_READER)]
    proc, port = _start(tmp_path, _manifest(tmp_path, members), {"FUNCD_PROBE_KEEP": "kept"})
    try:
        for name in ("before", "after"):
            st, body = _post(port, name, "{}")
            assert st == 200, body
            assert json.loads(body) == {
                "FUNCD_PROBE_LEAK": [None, None],
                "FUNCD_PROBE_KEEP": ["kept", "kept"],
            }, name
        st, body = _post(port, "writer", "{}")
        assert st == 200, body
        assert json.loads(body) == {"leak": "from-writer", "keep": None, "refused": ["putenv", "unsetenv"]}
    finally:
        proc.terminate()
        proc.wait(timeout=5)


CHILD_SPAWNER = (
    "import os\n"
    "import subprocess\n"
    "import time\n"
    "os.environ['FUNCD_PROBE_CHILD'] = 'from-member'\n"
    "os.environ['TZ'] = 'EST+5'\n"
    "time.tzset()\n"
    "ECHO = 'printf %s \"$FUNCD_PROBE_CHILD\"'\n"
    "def run(env=None):\n"
    "    return subprocess.run(['/bin/sh', '-c', ECHO], env=env, capture_output=True, text=True).stdout\n"
    "def handle(context, event):\n"
    "    out = os.path.join(event['data'], 'child-')\n"
    "    os.system(ECHO + ' > ' + out + 'system')\n"
    "    os.waitpid(os.posix_spawn('/bin/sh', ['sh', '-c', ECHO + ' > ' + out + 'spawn'], None), 0)\n"
    "    seen = {tag: open(out + tag).read() for tag in ('system', 'spawn')}\n"
    "    seen['run'] = run()\n"
    "    seen['explicit'] = run({'FUNCD_PROBE_CHILD': 'explicit'})\n"
    "    seen['popen'] = os.popen(ECHO).read()\n"
    "    seen['status'] = os.system('exit 3')\n"
    "    seen['hour'] = time.localtime(0).tm_hour\n"
    "    return seen\n"
)


def test_issue_r31_member_children_get_the_member_environ(tmp_path: Path) -> None:
    # A Node worker's child_process gets the worker's process.env copy, so a member's child processes get
    # the member's os.environ. The local time zone stays the process's: tzset() reads the process TZ.
    proc, port = _start(tmp_path, _manifest(tmp_path, [("spawner", CHILD_SPAWNER)]), {"TZ": "UTC0"})
    try:
        st, body = _post(port, "spawner", json.dumps({"data": str(tmp_path)}))
        assert st == 200, body
        assert json.loads(body) == {
            "system": "from-member",
            "spawn": "from-member",
            "run": "from-member",
            "popen": "from-member",
            "explicit": "explicit",
            "status": 3 << 8,
            "hour": 0,
        }
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


def test_pool_checks_no_contract(tmp_path: Path) -> None:
    # funcd ADR-0303: funcd checks each call's input and result, so a member runs its handler whatever the
    # contract its manifest names or a validator the module still exports would say.
    art = tmp_path / "baked.py"
    art.write_text("def __funcd_validate_input(d):\n    return ['rejected']\n" + ECHO)
    cpath = tmp_path / "contract.json"
    cpath.write_text(json.dumps({"input": {"type": "null"}, "output": {"type": "null"}}))
    mpath = tmp_path / "m.json"
    mpath.write_text(
        json.dumps([{"name": "f0", "artifact": str(art), "handler": "handle", "contract": str(cpath)}])
    )
    proc, port = _start(tmp_path, mpath)
    try:
        st, body = _post(port, "f0", json.dumps({"data": {"hello": 5}}))
        assert st == 200, body
        assert json.loads(body) == {"echoed": {"hello": 5}}
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _rows(tmp: Path, rows: list[dict[str, object]]) -> Path:
    """A manifest of *rows*, each {name, body, ...extra manifest keys}."""
    entries = []
    for row in rows:
        art = tmp / f"{row['name']}.py"
        art.write_text(str(row["body"]))
        extra = {k: v for k, v in row.items() if k not in ("name", "body")}
        entries.append({"name": row["name"], "artifact": str(art), "handler": "handle", **extra})
    mpath = tmp / "rows.json"
    mpath.write_text(json.dumps(entries))
    return mpath


def _get(port: int, path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:  # noqa: S310 - loopback
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _settled(port: int, timeout: float = 20) -> list[dict[str, str]]:
    """Wait until no member is loading, then return /health/members."""
    deadline = time.time() + timeout
    while _get(port, "/health/readiness")[0] != 200:
        assert time.time() < deadline, "a member is still loading"
        time.sleep(0.05)
    status, body = _get(port, "/health/members")
    assert status == 200
    members: list[dict[str, str]] = json.loads(body)
    return members


@pytest.fixture
def local_api(sock_dir: Path) -> Iterator[tuple[str, list[str]]]:
    """A fake worker-node local API: it records which member each call named and answers every kv,
    blob and invoke call with that member's name (403 when none is named)."""
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002 - stdlib signature
            return

        def _answer(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            self.rfile.read(length)
            member = self.headers.get("X-Funcd-Member")
            calls.append(f"{self.command} {self.path} {member or '-'}")
            if member is None:
                body, status = b"no member", 403
            elif self.path.startswith("/invoke/"):
                body, status = json.dumps({"via": member}).encode(), 200
            else:
                body, status = f"value-for-{member}".encode(), 200
            self.send_response(status)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer  # noqa: N815 - stdlib names

    path = str(sock_dir / "api.sock")
    server = UnixHTTPServer(path, Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield path, calls
    finally:
        server.shutdown()
        server.server_close()


_LOCAL_API = (
    "def handle(context, event):\n"
    "    return {\n"
    "        'kv': context.kv.get_str('t', 'k'),\n"
    "        'blob': context.blob.get('raw', 'x').decode(),\n"
    "        'invoke': context.invoke('peer', {}),\n"
    "    }\n"
)


def test_scenario_pooled_member_kv(tmp_path: Path, local_api: tuple[str, list[str]]) -> None:
    # scenario: pooled-member-kv — each member's context.kv/blob/invoke call names that member.
    socket_path, calls = local_api
    mpath = _rows(tmp_path, [{"name": "a", "body": _LOCAL_API}, {"name": "b", "body": _LOCAL_API}])
    proc, port = _start(tmp_path, mpath, env={"FUNCD_INVOKE_SOCKET": socket_path})
    try:
        _settled(port)
        calls.clear()  # /health/members asks each member's dependency check (funcd ADR-0215)
        for name in ("a", "b"):
            status, body = _post(port, name, "{}")
            assert status == 200, body
            assert json.loads(body) == {
                "kv": f"value-for-{name}",
                "blob": f"value-for-{name}",
                "invoke": {"via": name},
            }
        assert sorted(calls) == [
            "GET /blob/raw/x a",
            "GET /blob/raw/x b",
            "GET /kv/t/k a",
            "GET /kv/t/k b",
            "POST /invoke/peer a",
            "POST /invoke/peer b",
        ]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_scenario_pooled_member_logs(tmp_path: Path) -> None:
    # scenario: pooled-member-logs — a member's log records and span carry its own name.
    log = "import logging\ndef handle(context, event):\n    logging.info('hello-%s', event['data'])\n"
    channel = tmp_path / "channel"
    fd = os.open(channel, os.O_WRONLY | os.O_CREAT, 0o600)
    mpath = _rows(tmp_path, [{"name": "a", "body": log}, {"name": "b", "body": log}])
    proc, port = _start(tmp_path, mpath, env={"FUNCD_LOG_FD": str(fd)}, pass_fds=(fd,))
    try:
        _settled(port)
        assert _post(port, "a", json.dumps({"data": "a"}))[0] == 204
        assert _post(port, "b", json.dumps({"data": "b"}))[0] == 204
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        os.close(fd)
    records = [json.loads(line) for line in channel.read_text().splitlines() if line]
    logs = [(r["body"], r.get("funcd.member")) for r in records if "funcd.signal" not in r]
    spans = sorted((r["name"], r.get("funcd.member")) for r in records if r.get("funcd.signal") == "traces")
    assert logs == [("hello-a", "a"), ("hello-b", "b")]
    assert spans == [("a", "a"), ("b", "b")]


def test_scenario_pool_member_load_failure(tmp_path: Path) -> None:
    # scenario: pool-member-load-failure — a member with no handle export fails alone; the host does
    # not exit and its siblings serve.
    ok = "def handle(context, event):\n    return {'ok': True}\n"
    mpath = _rows(
        tmp_path,
        [{"name": "a", "body": ok}, {"name": "b", "body": "x = 1\n"}, {"name": "c", "body": ok}],
    )
    proc, port = _start(tmp_path, mpath)
    try:
        members = _settled(port)
        assert [(m["name"], m["state"]) for m in members] == [("a", "ready"), ("b", "failed"), ("c", "ready")]
        assert "handle" in members[1]["error"]
        assert _post(port, "a", "{}")[0] == 200
        assert _post(port, "c", "{}")[0] == 200
        assert _post(port, "b", "{}") == (503, b'{"error": "function b unavailable"}')
        assert proc.poll() is None, "the host keeps running"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_member_whose_import_hangs_fails_with_load_timed_out(tmp_path: Path) -> None:
    hung = "import time\ntime.sleep(4)\ndef handle(context, event):\n    return None\n"
    ok = "def handle(context, event):\n    return {'ok': True}\n"
    mpath = _rows(tmp_path, [{"name": "hung", "body": hung}, {"name": "ok", "body": ok}])
    proc, port = _start(tmp_path, mpath, env={"FUNCD_POOL_LOAD_TIMEOUT_MS": "1000"}, settle=False)
    try:
        assert _get(port, "/health/liveness")[0] == 200, "the host answers while members load"
        assert _get(port, "/health/readiness")[0] == 503, "a member is still loading"
        assert _settled(port) == [
            {"name": "hung", "state": "failed", "error": "load timed out"},
            {"name": "ok", "state": "ready"},
        ]
        assert _post(port, "ok", "{}")[0] == 200
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_member_env_reaches_only_that_member(tmp_path: Path) -> None:
    show = (
        "import os\n"
        "def handle(context, event):\n"
        "    return {'v': os.environ.get('FUNCD_TEST_MEMBER_VALUE')}\n"
    )
    mpath = _rows(
        tmp_path,
        [
            {"name": "a", "body": show, "env": {"FUNCD_TEST_MEMBER_VALUE": "a-only"}},
            {"name": "b", "body": show},
        ],
    )
    proc, port = _start(tmp_path, mpath)
    try:
        _settled(port)
        assert _post(port, "a", "{}") == (200, b'{"v":"a-only"}')
        assert _post(port, "b", "{}") == (200, b'{"v":null}')
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_member_stdout_is_line_buffered(tmp_path: Path) -> None:
    show = "import sys\ndef handle(context, event):\n    return {'v': sys.stdout.line_buffering}\n"
    mpath = _rows(tmp_path, [{"name": "a", "body": show}])
    proc, port = _start(tmp_path, mpath)
    try:
        _settled(port)
        assert _post(port, "a", "{}") == (200, b'{"v":true}')
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_load_timeout_follows_the_env() -> None:
    assert load_timeout_s("250") == 0.25
    for value in (None, "", "0", "-1", "1.5", "abc", "\u0661"):
        assert load_timeout_s(value) == 60.0, value


_OK = "def handle(context, event):\n    return {'ok': True}\n"
_KV_REPORT = {"kind": "kv", "binding": "audit", "reason": "Forbidden", "message": "kv::read denied"}


def test_scenario_health_pool_member_dependency(tmp_path: Path, dependency_api: DependencyAPI) -> None:
    # scenario: health-pool-member-dependency — only the member whose check fails carries a dependency;
    # a passing member and one whose funcd answers 404 (health-shim-compat) carry none.
    dependency_api.replies["a"] = (503, json.dumps(_KV_REPORT).encode())
    dependency_api.replies["c"] = (404, b"")
    mpath = _rows(tmp_path, [{"name": n, "body": _OK} for n in ("a", "b", "c")])
    proc, port = _start(tmp_path, mpath, env={"FUNCD_INVOKE_SOCKET": dependency_api.path})
    try:
        dependency_api.calls.clear()
        status, body = _get(port, "/health/members")
        assert status == 200
        assert json.loads(body) == [
            {"name": "a", "state": "ready", "dependency": _KV_REPORT},
            {"name": "b", "state": "ready"},
            {"name": "c", "state": "ready"},
        ]
        assert sorted(dependency_api.calls) == [
            "GET /health/dependencies a",
            "GET /health/dependencies b",
            "GET /health/dependencies c",
        ]
        assert _get(port, "/health/readiness") == (200, b"ready"), "the host's readiness is unchanged"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_members_dependency_check_asks_every_member(
    tmp_path: Path, dependency_api: DependencyAPI
) -> None:
    # funcd ADR-0215 Decision 4: the check is asked of all manifest members, a member that failed to load too.
    mpath = _rows(tmp_path, [{"name": "a", "body": _OK}, {"name": "b", "body": "x = 1\n"}])
    proc, port = _start(tmp_path, mpath, env={"FUNCD_INVOKE_SOCKET": dependency_api.path})
    try:
        members = _settled(port)
        assert [(m["name"], m["state"]) for m in members] == [("a", "ready"), ("b", "failed")]
        dependency_api.calls.clear()
        status, _ = _get(port, "/health/members")
        assert status == 200
        assert sorted(dependency_api.calls) == ["GET /health/dependencies a", "GET /health/dependencies b"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_members_dependency_check_has_one_bound(tmp_path: Path, dependency_api: DependencyAPI) -> None:
    # Every member is asked at once under one DependencyCheckBudget (50 ms), so /health/members fits
    # funcd's 100 ms probeTimeout even when the check of a member never answers.
    dependency_api.replies["slow1"] = HOLD
    dependency_api.replies["slow2"] = HOLD
    mpath = _rows(tmp_path, [{"name": n, "body": _OK} for n in ("slow1", "slow2", "fast")])
    proc, port = _start(tmp_path, mpath, env={"FUNCD_INVOKE_SOCKET": dependency_api.path})
    try:
        start = time.monotonic()
        status, body = _get(port, "/health/members")
        elapsed = time.monotonic() - start
        assert status == 200
        members = json.loads(body)
        assert elapsed < 0.1, elapsed
        assert [m["name"] for m in members] == ["slow1", "slow2", "fast"]
        for slow in members[:2]:
            dep = slow["dependency"]
            assert (dep["kind"], dep["binding"], dep["reason"]) == ("socket", "", "Timeout")
            assert dep["message"]
        assert members[2] == {"name": "fast", "state": "ready"}
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_liveness_and_readiness_never_call_funcd(tmp_path: Path, dependency_api: DependencyAPI) -> None:
    mpath = _rows(tmp_path, [{"name": "a", "body": _OK}])
    proc, port = _start(tmp_path, mpath, env={"FUNCD_INVOKE_SOCKET": dependency_api.path})
    try:
        dependency_api.calls.clear()
        assert _get(port, "/health/liveness") == (200, b"ok")
        assert _get(port, "/health/readiness") == (200, b"ready")
        assert dependency_api.calls == []
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ---- funcd ADR-0226: a late call is stopped in its member; one that will not stop fails liveness ----

LOOP = (
    "import funcd_shim\n"
    "def handle(context, event):\n"
    "    if event['data'] == 'ping':\n"
    "        return {'pong': True}\n"
    "    try:\n"
    "        while True:\n"
    "            pass\n"
    "    except funcd_shim.DeadlineExceeded:\n"
    "        with open(event['data'], 'w') as f:\n"
    "            f.write('saw DeadlineExceeded')\n"
    "        raise\n"
)
REMAINING = "def handle(context, event):\n    return {'ms': context.remaining_ms()}\n"
STOPPED = (500, b'{"error":"deadline exceeded"}')


def test_scenario_stuck_pooled_python_member_stopped(tmp_path: Path) -> None:
    # scenario: stuck-pooled-python-member-stopped — a watchdog in a's own subinterpreter stops a's loop at
    # its deadline + 1 s; a serves its next call, and b serves throughout.
    proc, port = _start(
        tmp_path, _rows(tmp_path, [{"name": "a", "body": LOOP}, {"name": "b", "body": REMAINING}])
    )
    seen = tmp_path / "seen"
    replies: list[tuple[int, bytes]] = []
    stopped = threading.Thread(
        target=lambda: replies.append(_post(port, "a", json.dumps({"data": str(seen)}), {TIMEOUT: "1000"}))
    )
    try:
        start = time.monotonic()
        stopped.start()
        while stopped.is_alive():
            assert _post(port, "b", "{}") == (200, b'{"ms":null}')
            time.sleep(0.1)
        assert time.monotonic() - start >= 2.0
        assert replies == [STOPPED]
        assert seen.read_text() == "saw DeadlineExceeded"
        assert _post(port, "a", json.dumps({"data": "ping"})) == (200, b'{"pong":true}')
        # scenario: handler-reads-deadline (pooled)
        status, body = _post(port, "b", "{}", {TIMEOUT: "5000"})
        assert status == 200
        assert 3000 < json.loads(body)["ms"] <= 5000
        assert _get(port, "/health/liveness") == (200, b"ok")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        stopped.join(timeout=5)


SLOW = (
    "import time\n"
    "def handle(context, event):\n"
    "    with open(event['data']['log'], 'a') as f:\n"
    "        f.write(event['data']['tag'] + '\\n')\n"
    "    time.sleep(event['data']['sleep'])\n"
    "    return {'ran': event['data']['tag']}\n"
)


def test_pooled_call_that_starts_late_is_not_run(tmp_path: Path) -> None:
    # A call queued behind its member's busy call starts after its deadline + 1 s: it answers as stopped,
    # and its handler never runs.
    proc, port = _start(tmp_path, _rows(tmp_path, [{"name": "a", "body": SLOW}]))
    log = tmp_path / "log"
    replies: list[tuple[int, bytes]] = []
    busy = threading.Thread(
        target=lambda: replies.append(
            _post(port, "a", json.dumps({"data": {"log": str(log), "tag": "busy", "sleep": 1.5}}))
        )
    )
    try:
        busy.start()
        until = time.monotonic() + 10
        while not (log.exists() and log.read_text()):
            assert time.monotonic() < until, "the busy call never started"
            time.sleep(0.01)
        late = json.dumps({"data": {"log": str(log), "tag": "late", "sleep": 0}})
        assert _post(port, "a", late, {TIMEOUT: "1"}) == STOPPED
        busy.join(timeout=10)
        assert replies == [(200, b'{"ran":"busy"}')]
        assert log.read_text() == "busy\n"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        busy.join(timeout=5)


def test_pool_liveness_fails_while_a_member_call_is_stuck(tmp_path: Path) -> None:
    # A raise cannot reach a handler blocked in C: the host's liveness answers 503 once the member still
    # runs the call STUCK_DELAY_S after its stop, while the sibling serves.
    sleeper = "import time\ndef handle(context, event):\n    time.sleep(3600)\n"
    mpath = _rows(tmp_path, [{"name": "a", "body": sleeper}, {"name": "b", "body": REMAINING}])
    proc, port = _start(tmp_path, mpath, constants={"STUCK_DELAY_S": 0.3})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        start = time.monotonic()
        conn.request("POST", "/function/a", b"{}", {TIMEOUT: "100"})
        while (live := _get(port, "/health/liveness")) == (200, b"ok"):
            assert time.monotonic() - start < 5, "liveness never failed"
            time.sleep(0.05)
        assert live == (503, b"stuck call")
        assert time.monotonic() - start >= 0.1 + 1 + 0.3
        assert _post(port, "b", "{}") == (200, b'{"ms":null}')
    finally:
        conn.close()
        proc.terminate()
        proc.wait(timeout=5)


def test_pool_liveness_counts_a_running_call_not_a_queued_one() -> None:
    pooled = _Pooled(SRC, "artifact.py", "handle")
    queued: Future[dict[str, object]] = Future()
    running: Future[dict[str, object]] = Future()
    running.set_running_or_notify_cancel()
    pooled._deadlines.update({queued: 0.0, running: 5.0})
    stuck_at = 5.0 + deadline.STOP_MARGIN_S + deadline.STUCK_DELAY_S
    assert not pooled.stuck(stuck_at - 0.001)
    assert pooled.stuck(stuck_at)
    del pooled._deadlines[running]
    assert not pooled.stuck(stuck_at + 3600), "a queued call never counts"


def test_pool_host_as_pid_1_exits_143_on_sigterm_with_a_call_in_flight(tmp_path: Path) -> None:
    # As the solo shim: the pool host is PID 1 in a container. A member's stuck call must not hold up the
    # exit either, which a normal exit would, joining the member's thread.
    started = tmp_path / "started"
    body = (
        "import time\n"
        "def handle(context, event):\n"
        "    open(event['data'], 'w').close()\n"
        "    time.sleep(3600)\n"
    )
    proc, port = _start(tmp_path, _rows(tmp_path, [{"name": "a", "body": body}]), pid1=True)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", "/function/a", json.dumps({"data": str(started)}).encode())
        until = time.monotonic() + 5
        while not started.exists():
            assert time.monotonic() < until, "the call never started"
            time.sleep(0.01)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=5) == 143
    finally:
        conn.close()
        proc.kill()
        proc.wait(timeout=5)


CTX_READER = (
    "def handle(context, event):\n"
    "    return {'lake': context.lake.get('lake'), 'level': context.config.get('LOG_LEVEL'),\n"
    "            'key': context.secret.get('STRIPE_KEY')}\n"
)


def test_ctx_pooled_member(tmp_path: Path, dependency_api: DependencyAPI) -> None:
    # scenario: ctx-pooled-member — each member reads its own lake token and the pool's config (funcd
    # ADR-0245), and reads its secrets from funcd as itself (funcd ADR-0249): the fake names the member.
    for name in ("a", "b"):
        dependency_api.replies[name] = (200, json.dumps({"value": f"sk-{name}"}).encode())
    entries = []
    for name in ("a", "b"):
        art = tmp_path / f"{name}.py"
        art.write_text(CTX_READER)
        entries.append(
            {
                "name": name,
                "artifact": str(art),
                "handler": "handle",
                "env": {"FUNCD_LAKE_LAKE_TOKEN": f"tok-{name}"},
            }
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(entries))
    shared = {
        "LOG_LEVEL": "debug",
        "FUNCD_CONFIG_KEYS": "LOG_LEVEL",
        "FUNCD_LAKE_LAKE_URL": "127.0.0.1:9100",
        "FUNCD_INVOKE_SOCKET": dependency_api.path,
    }
    proc, port = _start(tmp_path, manifest, env=shared)
    try:
        for name in ("a", "b"):
            st, body = _post(port, name, json.dumps({"data": {}}))
            assert st == 200, body
            assert json.loads(body) == {
                "lake": {"url": "127.0.0.1:9100", "token": f"tok-{name}"},
                "level": "debug",
                "key": f"sk-{name}",
            }
    finally:
        proc.terminate()
        proc.wait(timeout=10)
