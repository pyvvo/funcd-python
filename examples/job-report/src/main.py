"""report: a Job's handler that reports on one month (funcd ADR-0347).

It logs the month with the name of its run, works for a few seconds, so that a cancel can catch the run
while it runs, and returns one row for each day of the month. Its preview answers the same at once, for a
person to approve when the Job has no ``approval.bypass`` (funcd ADR-0348). ``JobInput`` and ``JobOutput``
are its input and output, which ``pyreport.contract.yaml`` states as the artifact's contract.
"""

import calendar
import re
import time
from typing import TypedDict

from funcd_shim import CloudEvent, JobContext


# region types
class JobInput(TypedDict):
    """The month to report on, as YYYY-MM."""

    month: str


class JobOutput(TypedDict):
    """The month, and one row for each of its days."""

    month: str
    rows: int


# endregion types

# How long a report works, in seconds; the tests set it to 0.
WORK_SECONDS = 3.0


def _rows(month: str) -> int:
    """The number of days of month, YYYY-MM; any other month fails the call."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError(f'month "{month}" is not YYYY-MM')
    year, mm = (int(part) for part in month.split("-"))
    return calendar.monthrange(year, mm)[1]


# region handler
def handle(context: JobContext, event: CloudEvent[JobInput]) -> JobOutput:
    month = event["data"]["month"]
    rows = _rows(month)
    context.log(f"building the report of {month} in run {context.run['name']}")
    time.sleep(WORK_SECONDS)
    return {"month": month, "rows": rows}


# endregion handler


# region preview
def preview(context: JobContext, event: CloudEvent[JobInput]) -> JobOutput:
    """A run's preview, which a person approves before a Job without ``approval.bypass`` runs (funcd
    ADR-0348): what the report would hold, at once and with no work."""
    month = event["data"]["month"]
    return {"month": month, "rows": _rows(month)}


# endregion preview
