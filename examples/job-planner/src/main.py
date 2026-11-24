"""pyplanner: a Function that starts a run of the Job pyreport and reads it back (funcd ADR-0349).

For the month of its event, it starts a run of the Job that its Function binds under the alias ``report``,
named after the month, and reads the run's phase back. A start sent again for one month, with its input,
returns the run that the first start made instead of starting a second one. ``PlanInput`` and ``PlanOutput``
are its input and output, which ``pyplanner.contract.yaml`` states as the artifact's contract.
"""

from typing import TypedDict

from funcd_shim import CloudEvent, FunctionContext


# region types
class PlanInput(TypedDict):
    """The month to report on, as YYYY-MM."""

    month: str


class PlanOutput(TypedDict):
    """The name of the run of pyreport, and its phase when the planner read it back."""

    run: str
    phase: str


# endregion types


# region handler
def handle(context: FunctionContext, event: CloudEvent[PlanInput]) -> PlanOutput:
    month = event["data"]["month"]
    run = context.job.start("report", {"month": month}, f"pyreport-{month}")
    return {"run": run, "phase": context.job.get("report", run)["phase"]}


# endregion handler
