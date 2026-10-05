"""Path B function-log capture tests (ADR-0081 python-logging-structured).

The Python shim has no ``console``; its structured capture seam is a ``logging.Handler`` installed on
the root logger by ``install_log_capture()``. These tests select the channel via the env contract
(``FUNCD_LOG_FD`` to a pipe, ``FUNCD_LOG_SOCK`` to a temp Unix socket), emit logs, and assert the host
sees the exact NDJSON wire: one object per line, ``"funcd.source": "logging"``, mapped severities,
``body`` = the formatted message, and ``attrs`` carrying the stringified ``extra`` fields."""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
from collections.abc import Buffer, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from funcd_shim.funclog import FuncLogHandler, _record_bound, install_log_capture


@pytest.fixture(autouse=True)
def _clean_root_logger() -> Iterator[None]:
    """Restore the root logger after each test (the handler is installed on the process-wide root)."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def _parse_lines(raw: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]


def test_no_channel_env_is_noop(monkeypatch: Any) -> None:
    monkeypatch.delenv("FUNCD_LOG_FD", raising=False)
    monkeypatch.delenv("FUNCD_LOG_SOCK", raising=False)
    before = logging.getLogger().handlers[:]
    assert install_log_capture() is None
    assert logging.getLogger().handlers == before  # nothing installed


def test_fd_channel_captures_two_structured_lines(monkeypatch: Any) -> None:
    read_fd, write_fd = os.pipe()
    monkeypatch.delenv("FUNCD_LOG_SOCK", raising=False)
    monkeypatch.setenv("FUNCD_LOG_FD", str(write_fd))

    handler = install_log_capture()
    assert handler is not None

    root = logging.getLogger()
    root.info("user %s", "alice", extra={"id": "7"})
    logging.error("boom")

    os.close(write_fd)  # EOF for the reader
    raw = b""
    while chunk := os.read(read_fd, 4096):
        raw += chunk
    os.close(read_fd)

    records = _parse_lines(raw)
    assert len(records) == 2, records

    first, second = records

    assert first["body"] == "user alice"  # %s expanded by record.getMessage()
    assert first["sev"] == "INFO"
    assert first["funcd.source"] == "logging"
    assert first["attrs"]["id"] == "7"  # the extra= field, stringified
    assert first["attrs"]["logger"] == "root"
    assert "lineno" in first["attrs"]
    assert isinstance(first["ts"], int) and first["ts"] > 0
    assert first["inv"] == "" and first["trace_id"] == "" and first["span_id"] == ""

    assert second["body"] == "boom"
    assert second["sev"] == "ERROR"
    assert second["funcd.source"] == "logging"


def test_severity_mapping(monkeypatch: Any) -> None:
    read_fd, write_fd = os.pipe()
    monkeypatch.delenv("FUNCD_LOG_SOCK", raising=False)
    monkeypatch.setenv("FUNCD_LOG_FD", str(write_fd))
    assert install_log_capture() is not None

    log = logging.getLogger()
    log.debug("d")
    log.info("i")
    log.warning("w")
    log.error("e")
    log.critical("c")

    os.close(write_fd)
    raw = b""
    while chunk := os.read(read_fd, 4096):
        raw += chunk
    os.close(read_fd)

    sevs = [r["sev"] for r in _parse_lines(raw)]
    # root level is raised to INFO by install_log_capture, so DEBUG is filtered out.
    assert sevs == ["INFO", "WARN", "ERROR", "FATAL"]


def test_uds_channel_captures_lines(monkeypatch: Any, sock_dir: Path) -> None:
    sock_path = sock_dir / "log.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)

    received: list[bytes] = []

    def accept_and_drain() -> None:
        conn, _ = server.accept()
        with conn:
            while chunk := conn.recv(4096):
                received.append(chunk)

    reader = threading.Thread(target=accept_and_drain, daemon=True)
    reader.start()

    monkeypatch.delenv("FUNCD_LOG_FD", raising=False)
    monkeypatch.setenv("FUNCD_LOG_SOCK", str(sock_path))
    handler = install_log_capture()
    assert handler is not None

    logging.getLogger().warning("hello %d", 42, extra={"k": "v"})

    # Close the channel socket so the server side sees EOF, then join the reader.
    handler._channel._sock.close()  # type: ignore[attr-defined]  # test reaches into the concrete _Channel
    reader.join(timeout=5)
    server.close()

    records = _parse_lines(b"".join(received))
    assert len(records) == 1
    rec = records[0]
    assert rec["body"] == "hello 42"
    assert rec["sev"] == "WARN"
    assert rec["funcd.source"] == "logging"
    assert rec["attrs"]["k"] == "v"


def _capture(monkeypatch: Any, emit: Callable[[], None]) -> list[dict[str, Any]]:
    read_fd, write_fd = os.pipe()
    monkeypatch.delenv("FUNCD_LOG_SOCK", raising=False)
    monkeypatch.setenv("FUNCD_LOG_FD", str(write_fd))
    assert install_log_capture() is not None
    emit()
    os.close(write_fd)
    raw = b""
    while chunk := os.read(read_fd, 4096):
        raw += chunk
    os.close(read_fd)
    return _parse_lines(raw)


def test_issue_82_record_args_reach_attrs(monkeypatch: Any) -> None:
    def emit() -> None:
        logging.info("py-logging-info %s %d", "arg1", 7)
        logging.info("%(user)s x%(n)d", {"user": "alice", "n": 2})
        logging.error("failed: %s", ValueError("second-boom"))
        logging.info("no args")

    positional, mapping, error_arg, bare = _capture(monkeypatch, emit)

    assert positional["body"] == "py-logging-info arg1 7"
    assert json.loads(positional["attrs"]["args"]) == ["arg1", 7]
    assert json.loads(mapping["attrs"]["args"]) == {"user": "alice", "n": 2}
    assert json.loads(error_arg["attrs"]["args"]) == ["ValueError('second-boom')"]
    assert "args" not in bare["attrs"]


def test_issue_82_traceback_reaches_attrs(monkeypatch: Any) -> None:
    def emit() -> None:
        try:
            raise ValueError("py-boom-detail")
        except ValueError:
            logging.exception("py-caught")
        logging.error("with stack", stack_info=True)

    caught, stacked = _capture(monkeypatch, emit)

    assert caught["body"] == "py-caught"
    assert caught["sev"] == "ERROR"
    assert caught["attrs"]["exception.type"] == "ValueError"
    assert caught["attrs"]["exception.message"] == "py-boom-detail"
    trace = caught["attrs"]["exception.stacktrace"]
    assert trace.startswith("Traceback (most recent call last):")
    assert trace.endswith("ValueError: py-boom-detail")
    assert stacked["attrs"]["code.stacktrace"].startswith("Stack (most recent call last):")
    assert "exception.stacktrace" not in stacked["attrs"]


def test_issue_r26_short_write_keeps_record_whole(monkeypatch: Any) -> None:
    real_write = os.write

    def short_write(fd: int, data: Buffer) -> int:
        # write(2) on a pipe returns a short count when a signal interrupts it after some bytes went out.
        return real_write(fd, memoryview(data)[:16])

    monkeypatch.setattr(os, "write", short_write)

    def emit() -> None:
        logging.info("first %s", "x" * 64)
        logging.info("second")

    first, second = _capture(monkeypatch, emit)

    assert first["body"] == "first " + "x" * 64
    assert second["body"] == "second"


class _Lines:
    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def write_line(self, line: bytes) -> None:
        self.lines.append(line)


def test_pool_member_is_stamped_and_solo_omits_it() -> None:
    sink = _Lines()
    FuncLogHandler(sink, "a").emit(logging.makeLogRecord({"msg": "pooled", "levelno": logging.INFO}))
    FuncLogHandler(sink).emit(logging.makeLogRecord({"msg": "solo", "levelno": logging.INFO}))
    pooled, solo = _parse_lines(b"".join(sink.lines))
    assert (pooled["body"], pooled["funcd.member"]) == ("pooled", "a")
    assert "funcd.member" not in solo


# ADR-0168: one log call yields one record of at most FUNCD_FUNCLOG_MAX_RECORD_BYTES bytes.


def _bounded_lines(bound: int = 65536) -> tuple[_Lines, logging.Logger]:
    sink = _Lines()
    logger = logging.getLogger("bound-test")
    logger.handlers[:] = [FuncLogHandler(sink, None, bound)]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return sink, logger


def _kept_bytes(rec: dict[str, Any]) -> int:
    """The marker's keptBytes recomputed: body plus every attr value but lineno and the markers."""
    skip = {"lineno", "truncated", "keptBytes"}
    values = [rec["body"], *(v for k, v in rec["attrs"].items() if k not in skip)]
    return sum(len(v.encode("utf-8", "surrogatepass")) for v in values)


def _assert_bounded(sink: _Lines, bound: int) -> list[dict[str, Any]]:
    recs = []
    for line in sink.lines:
        assert line.endswith(b"\n")
        assert len(line) - 1 <= bound, len(line)
        rec = json.loads(line)
        if rec["attrs"].get("truncated") == "true":
            assert int(rec["attrs"]["keptBytes"]) == _kept_bytes(rec)
        recs.append(rec)
    return recs


class _CountingDict(dict[str, object]):
    reads = 0

    def items(self) -> Iterator[tuple[str, object]]:  # type: ignore[override]
        for kv in super().items():
            _CountingDict.reads += 1
            yield kv


def _shared(levels: int) -> dict[str, object]:
    """2^levels leaves through dicts that share their children: about 3.5 MB of text at 16."""
    node: dict[str, object] = _CountingDict(leaf="x" * 40)
    for _ in range(levels):
        node = _CountingDict(a=node, b=node)
    return node


def test_scenario_record_cut_at_bound() -> None:
    sink, logger = _bounded_lines()
    _CountingDict.reads = 0
    logger.error("big", extra={"obj": _shared(16)})
    (rec,) = _assert_bounded(sink, 65536)
    assert (rec["sev"], rec["body"]) == ("ERROR", "big")
    assert rec["attrs"]["truncated"] == "true"
    assert int(rec["attrs"]["keptBytes"]) <= 65536
    assert rec["attrs"]["obj"].startswith("{'a': {'a': ")
    # A full walk reads each of the 2^17 shared dicts' entries; the cut stops it after a few thousand.
    assert _CountingDict.reads < 20_000, _CountingDict.reads


def test_scenario_record_bound_reaches_shim(monkeypatch: Any) -> None:
    monkeypatch.setenv("FUNCD_FUNCLOG_MAX_RECORD_BYTES", "8192")
    sink = _Lines()
    install_log_capture(sink)
    logging.getLogger().info("payload", extra={"blob": "y" * 100_000})
    (rec,) = _assert_bounded(sink, 8192)
    assert len(sink.lines[0]) > 8000  # the cut keeps what fits
    assert rec["attrs"]["truncated"] == "true"
    assert set(rec["attrs"]["blob"]) == {"y"}


def test_record_bound_follows_the_env(monkeypatch: Any) -> None:
    for raw in ("", "0", "-1", "1.5", "abc", "\u0661"):
        monkeypatch.setenv("FUNCD_FUNCLOG_MAX_RECORD_BYTES", raw)
        assert _record_bound() == 65536, raw
    monkeypatch.setenv("FUNCD_FUNCLOG_MAX_RECORD_BYTES", "8192")
    assert _record_bound() == 8192


def test_fifty_mb_bytes_stop_at_the_cut() -> None:
    sink, logger = _bounded_lines(4096)
    data = b"\x00'" * (25 << 20)
    logger.info("blob %s", data)
    logger.info("blob", extra={"data": bytearray(data)})
    body, extra = _assert_bounded(sink, 4096)
    assert body["body"].startswith("blob b\"\\x00'\\x00'")
    assert extra["attrs"]["data"].startswith("bytearray(b\"\\x00'")
    assert body["attrs"]["truncated"] == extra["attrs"]["truncated"] == "true"


def test_body_of_a_large_percent_s_argument_is_bounded() -> None:
    sink, logger = _bounded_lines(2048)
    logger.info("%s", {"k": ["v" * 100] * 1000})
    (rec,) = _assert_bounded(sink, 2048)
    assert rec["body"].startswith("{'k': ['vvvv")
    assert rec["attrs"]["truncated"] == "true"
    assert "args" not in rec["attrs"]  # filled last, nothing left


def test_bound_holds_for_non_ascii_astral_quote_heavy_text_and_a_long_logger() -> None:
    sink, logger = _bounded_lines(2048)
    for unit in ("\u00e9", "\u20ac", "\U0001f600", '"\\', "\x01\n", "\x7f"):
        logger.info(unit * 3000, extra={"k": unit * 3000})
        logger.info("short %s %r", unit * 200, [unit * 300], extra={"k": unit * 300})
    long_logger = logging.getLogger("l" * 5000)
    long_logger.handlers[:] = logger.handlers
    long_logger.propagate = False
    long_logger.setLevel(logging.DEBUG)
    long_logger.info("from a long logger")
    recs = _assert_bounded(sink, 2048)
    assert len(recs) == 13
    assert all(r["attrs"]["truncated"] == "true" for r in recs)
    assert recs[-1]["body"] == "from a long logger"


def test_today_text_for_small_records() -> None:
    sink, logger = _bounded_lines()
    logger.error(ValueError("boom"))
    logger.info("%r", "x")
    logger.info("%(k)s and %(n)d", {"k": "v", "n": 3})
    logger.info("%s|%5s|%-3s|%.2s|%d|%x|%%", "a", "b", "c", "dddd", 7, 255)
    logger.info("plain", extra={"truncated": "no", "keptBytes": "1", "ok": "yes"})
    boom, r, keyed, mixed, plain = _assert_bounded(sink, 65536)
    assert boom["body"] == "boom"
    assert r["body"] == "'x'"
    assert r["attrs"]["args"] == '["x"]'
    assert keyed["body"] == "v and 3"
    assert keyed["attrs"]["args"] == '{"k":"v","n":3}'
    assert mixed["body"] == "a|    b|c  |dd|7|ff|%"
    assert plain["attrs"]["ok"] == "yes"
    assert "truncated" not in plain["attrs"] and "keptBytes" not in plain["attrs"]
    assert list(plain["attrs"])[:3] == ["logger", "funcName", "lineno"]


def test_args_json_keeps_today_text_and_a_cycle_falls_back_to_repr() -> None:
    sink, logger = _bounded_lines()
    cyclic: list[object] = [1]
    cyclic.append(cyclic)
    logger.info("%s %s %s %s", {"a": [1, 2.5, None, True]}, b"\x01", {1: "x"}, "\u00e9")
    logger.info("%s", cyclic)
    plain, cycle = _assert_bounded(sink, 65536)
    args = ({"a": [1, 2.5, None, True]}, b"\x01", {1: "x"}, "\u00e9")
    assert plain["attrs"]["args"] == json.dumps(args, default=repr, ensure_ascii=False, separators=(",", ":"))
    assert cycle["attrs"]["args"] == repr((cyclic,))
    assert cycle["body"] == "[1, [...]]"
