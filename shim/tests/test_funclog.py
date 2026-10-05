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

from funcd_shim.funclog import install_log_capture


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
