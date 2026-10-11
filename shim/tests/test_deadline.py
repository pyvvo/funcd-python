"""Unit tests of a call's deadline (funcd ADR-0226): the header, the remaining time, and the end of a
watched call. The stops themselves are tested on the shim run as a subprocess (test_shim, test_pool)."""

from __future__ import annotations

import http.client
import io
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import pytest

from funcd_shim import DeadlineExceeded, deadline, runtime
from funcd_shim.invcontext import InvContext, remaining_ms, reset_inv, set_inv
from funcd_shim.types import CloudEvent, FunctionContext

CONTEXT = cast(FunctionContext, object())


@contextmanager
def call_with(deadline_at: float | None) -> Iterator[None]:
    """The per-call carrier as the shim sets it, with *deadline_at* on the time.monotonic() clock."""
    token = set_inv(InvContext("0" * 16, "0" * 32, "0" * 16, "", deadline_at))
    try:
        yield
    finally:
        reset_inv(token)


def headers(*lines: str) -> http.client.HTTPMessage:
    raw = "".join(f"{line}\r\n" for line in lines) + "\r\n"
    return http.client.parse_headers(io.BytesIO(raw.encode()))


@pytest.mark.parametrize(
    ("lines", "ms"),
    [
        (["X-Funcd-Timeout-Ms: 1"], 1),
        (["x-funcd-timeout-ms: 5000"], 5000),
        (["X-Funcd-Timeout-Ms: 2147482647"], 2_147_482_647),
        (["X-Funcd-Timeout-Ms: 007"], 7),
        (["X-Funcd-Timeout-Ms:  300 \t"], 300),
        ([], None),
        (["X-Funcd-Timeout-Ms: 0"], None),
        (["X-Funcd-Timeout-Ms: 2147482648"], None),
        (["X-Funcd-Timeout-Ms: -1"], None),
        (["X-Funcd-Timeout-Ms: +5"], None),
        (["X-Funcd-Timeout-Ms: 1.5"], None),
        (["X-Funcd-Timeout-Ms: 1e3"], None),
        (["X-Funcd-Timeout-Ms: "], None),
        (["X-Funcd-Timeout-Ms: ١"], None),
        (["X-Funcd-Timeout-Ms: 5, 6"], None),
        (["X-Funcd-Timeout-Ms: 5", "X-Funcd-Timeout-Ms: 6"], None),
    ],
)
def test_deadline_from_takes_the_typescript_pool_range(lines: list[str], ms: int | None) -> None:
    expected = None if ms is None else 100.0 + ms / 1000
    assert deadline.deadline_from(headers(*lines), 100.0) == expected


def test_remaining_ms_counts_down_from_the_carrier_floored(monkeypatch: pytest.MonkeyPatch) -> None:
    assert remaining_ms() is None, "outside a call"
    with call_with(None):
        assert remaining_ms() is None, "a call without the header"
    monkeypatch.setattr(time, "monotonic", lambda: 10.0)
    with call_with(10.0 + 1.9999):
        assert remaining_ms() == 1999
    with call_with(10.0 - 0.001):
        assert remaining_ms() == 0


def test_a_call_without_a_deadline_has_no_watchdog() -> None:
    watchdogs: list[list[str]] = []

    def handler(context: FunctionContext, event: CloudEvent[Any]) -> None:
        watchdogs.append([t.name for t in threading.enumerate() if t.name == "funcd-deadline"])

    runtime.call_handler(handler, CONTEXT, CloudEvent())
    with call_with(None):
        runtime.call_handler(handler, CONTEXT, CloudEvent())
    with call_with(time.monotonic() + 60):
        runtime.call_handler(handler, CONTEXT, CloudEvent())
    assert watchdogs == [[], [], ["funcd-deadline"]]


def test_a_call_that_starts_after_its_stop_does_not_run() -> None:
    ran: list[object] = []
    with (
        call_with(time.monotonic() - deadline.STOP_MARGIN_S),
        pytest.raises(DeadlineExceeded, match="^deadline exceeded$"),
    ):
        runtime.call_handler(lambda context, event: ran.append(event), CONTEXT, CloudEvent())
    assert ran == []


def test_end_finishes_when_the_raise_fires_inside_it() -> None:
    # The watchdog raises under the call's lock. A call whose end waits for that lock gets the raise just
    # after it takes the lock, where the with statement does not guard it yet: end() must still
    # deregister the call and stop its watchdog, and the thread's next call must run with no raise.
    returned, blocked = threading.Event(), threading.Event()
    outcome: dict[str, object] = {}

    def first(context: FunctionContext, event: CloudEvent[Any]) -> str:
        returned.set()
        blocked.wait(5)
        return "first"

    def second(context: FunctionContext, event: CloudEvent[Any]) -> str:
        until = time.monotonic() + 0.2
        while time.monotonic() < until:
            pass
        return "second"

    def calls() -> None:
        with call_with(time.monotonic() + 60):
            outcome["first"] = runtime.call_handler(first, CONTEXT, CloudEvent())
            outcome["second"] = runtime.call_handler(second, CONTEXT, CloudEvent())

    thread = threading.Thread(target=calls)
    thread.start()
    assert returned.wait(5)
    (call,) = deadline._in_flight
    with call._lock:
        blocked.set()
        frame = sys._current_frames()[cast(int, thread.ident)]
        until = time.monotonic() + 5
        while frame.f_code.co_name != "end":
            assert time.monotonic() < until, "the call never reached its end"
            time.sleep(0.01)
            frame = sys._current_frames()[cast(int, thread.ident)]
        call._raise()
    thread.join(5)
    assert not thread.is_alive()
    assert outcome == {"first": "first", "second": "second"}
    assert not deadline._in_flight
    assert [t for t in threading.enumerate() if t.name == "funcd-deadline"] == []
