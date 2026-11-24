"""The author's unit tests of the planner: a fake context.job keeps each start and answers each read."""

from typing import Any, cast

from funcd_shim import CloudEvent, FunctionContext, JobRunState

from main import PlanInput, handle


class _Jobs:
    def __init__(self) -> None:
        self.starts: list[tuple[str, Any, str | None]] = []

    def start(self, alias: str, input: Any = None, name: str | None = None) -> str:  # noqa: A002 - the shim's name
        self.starts.append((alias, input, name))
        return name or "pyreport-x1y2"

    def get(self, alias: str, run: str) -> JobRunState:
        return {
            "name": run,
            "phase": "Pending",
            "reason": "",
            "started_at": None,
            "ended_at": None,
            "result": None,
        }


class _Ctx:
    def __init__(self) -> None:
        self.job = _Jobs()


def _event(month: str) -> CloudEvent[PlanInput]:
    return {"id": "1", "source": "s", "type": "plan", "data": {"month": month}}


def test_the_planner_starts_the_month_run_and_reads_it_back() -> None:
    ctx = _Ctx()
    assert handle(cast(FunctionContext, ctx), _event("2026-10")) == {
        "run": "pyreport-2026-10",
        "phase": "Pending",
    }
    assert ctx.job.starts == [("report", {"month": "2026-10"}, "pyreport-2026-10")]


def test_an_event_sent_again_starts_the_run_under_the_same_name_with_the_same_input() -> None:
    ctx = _Ctx()
    handle(cast(FunctionContext, ctx), _event("2026-11"))
    handle(cast(FunctionContext, ctx), _event("2026-11"))
    assert ctx.job.starts[0] == ctx.job.starts[1], (
        "funcd answers the second start with the run that the first made"
    )
