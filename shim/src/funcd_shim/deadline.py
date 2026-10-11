"""A call's deadline (funcd ADR-0226): read from X-Funcd-Timeout-Ms, the per-call watchdog that stops a
late call in the call's own interpreter, and the calls in flight that liveness reads. The delays are
module constants, not settings. Stdlib only."""

from __future__ import annotations

import _thread
import asyncio
import contextlib
import ctypes
import threading
import time
from collections.abc import Callable, Coroutine
from email.message import Message
from typing import Any, cast

from .types import DeadlineExceeded

TIMEOUT_HEADER = "X-Funcd-Timeout-Ms"

STOP_MARGIN_S = 1.0
"""A late call is stopped this long after its deadline, once funcd has answered 504 (ADR-0151)."""

ESCALATION_S = 1.0
"""An async call that still runs this long after its task was cancelled gets the raise."""

STUCK_DELAY_S = 10.0
"""A call that still runs this long after its stop fails liveness, so funcd restarts the replica."""

# setTimeout's largest delay less the TypeScript pool's margin: the header range both pools accept.
_MAX_TIMEOUT_MS = 2_147_482_647
# A pool member's subinterpreter refuses daemon threads; in the main interpreter a watchdog must not
# delay the exit.
_DAEMON = _thread.daemon_threads_allowed()

# The calls in flight in this interpreter. Only single set operations change it, never under a lock
# shared by calls: a raise that fires in a thread holding such a lock would leave it held.
_in_flight: set[Call] = set()


def deadline_from(headers: Message, received: float) -> float | None:
    """The call's deadline on the ``time.monotonic()`` clock: *received* plus a valid
    X-Funcd-Timeout-Ms, a decimal integer in 1..2147482647 as the TypeScript pool reads it (Node joins
    a repeated header, which then fails); None without one."""
    values = headers.get_all(TIMEOUT_HEADER) or []
    value = values[0].strip(" \t") if len(values) == 1 else ""
    if not (value.isascii() and value.isdigit()):
        return None
    ms = int(value)
    return received + ms / 1000 if 1 <= ms <= _MAX_TIMEOUT_MS else None


def is_stuck(deadline: float, now: float) -> bool:
    """Whether a call with *deadline* that still runs at *now* runs STUCK_DELAY_S after its stop."""
    return now >= deadline + STOP_MARGIN_S + STUCK_DELAY_S


def stuck() -> bool:
    """Whether a call in flight in this interpreter is stuck, which fails liveness."""
    now = time.monotonic()
    return any(is_stuck(call.deadline, now) for call in _in_flight.copy())


def _set_async_exc(thread: int, exc: type[BaseException] | None) -> None:
    """Raise *exc* in *thread* at its next check point, or clear a pending raise with None. It finds
    only the threads of the calling interpreter."""
    ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(thread), None if exc is None else ctypes.py_object(exc)
    )


def _call_soon(loop: asyncio.AbstractEventLoop, callback: Callable[..., object], *args: object) -> None:
    with contextlib.suppress(RuntimeError):  # the loop closed as the call ended
        loop.call_soon_threadsafe(callback, *args)


def _wake() -> None:
    """A callback that wakes a loop waiting in select, so a raise in its thread fires."""


class Call:
    """A call with a deadline, run in the thread that created it, and its watchdog: a timer thread in
    the same interpreter that the call's end stops. Created after its stop, it raises
    DeadlineExceeded: the call does not run.

    At the stop the watchdog raises DeadlineExceeded in the call's thread. An async handler's task is
    cancelled first, through its loop, and the raise follows ESCALATION_S later only if the call still
    runs, as asyncio.Runner answers a second SIGINT.

    The raise and the end hold the call's lock, and the watchdog raises at most once, while the call
    is in flight. A raise fires at the thread's next check point, which can come right after end()
    took the lock, before the with statement guards it; nothing takes the lock once a raise fired, so
    end() then finishes without it."""

    def __init__(self, deadline: float) -> None:
        if time.monotonic() >= deadline + STOP_MARGIN_S:
            raise DeadlineExceeded
        self.deadline = deadline
        self._thread = threading.get_ident()
        self._loop_task: tuple[asyncio.AbstractEventLoop, asyncio.Task[Any]] | None = None
        self._lock = threading.Lock()
        self._raised = False
        self._ended = threading.Event()
        self._watchdog = threading.Thread(target=self._watch, name="funcd-deadline", daemon=_DAEMON)

    def start(self) -> None:
        _in_flight.add(self)
        self._watchdog.start()

    async def track[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Await an async handler's coroutine as the task the watchdog cancels."""
        self._loop_task = (asyncio.get_running_loop(), cast("asyncio.Task[Any]", asyncio.current_task()))
        return await coro

    def end(self) -> None:
        """End the call in its thread: deregister it and clear a raise that has not fired, then stop
        the watchdog."""
        try:
            with self._lock:
                _in_flight.discard(self)
                if self._raised:
                    _set_async_exc(self._thread, None)
        except DeadlineExceeded:
            _in_flight.discard(self)
        self._ended.set()
        if self._watchdog.is_alive():
            self._watchdog.join()

    def _watch(self) -> None:
        if self._ended.wait(self.deadline + STOP_MARGIN_S - time.monotonic()):
            return
        with self._lock:
            if self not in _in_flight:
                return
            if self._loop_task is None:
                self._raise()
                return
            loop, task = self._loop_task
            _call_soon(loop, task.cancel, "deadline exceeded")
        if self._ended.wait(ESCALATION_S):
            return
        with self._lock:
            if self in _in_flight:
                self._raise()
                _call_soon(loop, _wake)

    def _raise(self) -> None:
        self._raised = True
        _set_async_exc(self._thread, DeadlineExceeded)
