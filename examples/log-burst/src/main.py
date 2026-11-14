"""log-burst: a funcd Function that writes a burst of log records.

It logs 90 INFO, 7 WARNING and 3 ERROR records through ``logging``, each with attributes from ``extra=``,
prints one line to standard output and one to standard error, and returns ``{"emitted": <count>}``, the number
of logging calls.
"""

from __future__ import annotations

import logging
import sys
from typing import TypedDict

from funcd_shim import CloudEvent, FunctionContext

# The records of this logger reach the root logger, where funcd takes them.
log = logging.getLogger("log-burst")

# How many records of each level to log: 90 INFO, 7 WARNING and 3 ERROR; the input's count adds INFO.
_BASE_INFO = 90
_WARNINGS = 7
_ERRORS = 3


class FuncInput(TypedDict, total=False):
    """The call's data, every key optional: ``count`` adds INFO records; ``big`` logs one value of that many
    bytes, to show a record cut at its size limit."""

    count: int
    big: int


class FuncOutput(TypedDict):
    """The response: how many logging calls the handler made."""

    emitted: int


def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    """Emit a burst of ≥ 100 structured log records, then report the count."""
    data = event.get("data") or {}
    extra_info = max(int(data.get("count", 0)), 0)
    info_count = _BASE_INFO + extra_info

    # a line on standard output becomes an INFO record, one on standard error an ERROR record.
    print("burst stdout", flush=True)
    print("burst stderr", file=sys.stderr, flush=True)

    emitted = 0
    big = max(int(data.get("big", 0)), 0)
    if big:
        log.info("big value", extra={"big": "x" * big})
        emitted += 1
    for i in range(info_count):
        # the extra= fields become the record's attributes
        log.info("processing item %d", i, extra={"item": i, "phase": "scan"})
        emitted += 1
    for i in range(_WARNINGS):
        log.warning("slow item %d took %dms", i, 120 + i, extra={"item": i, "phase": "scan"})
        emitted += 1
    for i in range(_ERRORS):
        log.error("item %d failed validation", i, extra={"item": i, "phase": "validate"})
        emitted += 1

    return {"emitted": emitted}
