"""report: a Job's handler that reports on one month (funcd ADR-0347).

It logs the month with the name of its run, works for a few seconds, so that a cancel can catch the run
while it runs, and returns one row for each day of the month. ``JobInput`` and ``JobOutput`` are its input
and output, which ``pyreport.contract.yaml`` states as the artifact's contract.
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


# region handler
def handle(context: JobContext, event: CloudEvent[JobInput]) -> JobOutput:
    month = event["data"]["month"]
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError(f'month "{month}" is not YYYY-MM')
    year, mm = (int(part) for part in month.split("-"))
    context.log(f"building the report of {month} in run {context.run['name']}")
    time.sleep(WORK_SECONDS)
    return {"month": month, "rows": calendar.monthrange(year, mm)[1]}


# endregion handler
