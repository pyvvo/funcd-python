"""The author's unit tests of the report handler: a fake context keeps the log lines, and the work takes
no time."""

from typing import cast

import pytest
from funcd_shim import CloudEvent, JobContext, RunInfo

import main
from main import JobInput, handle, preview


class _Ctx:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.run: RunInfo = {
            "name": "pyreport-1",
            "job": "pyreport",
            "revision": "pyreport-v1",
            "phase": "run",
            "started_by": "dev",
        }

    def log(self, *args: object) -> None:
        self.lines.append(" ".join(str(a) for a in args))


def _event(month: str) -> CloudEvent[JobInput]:
    return {"id": "1", "source": "s", "type": "funcd.job.run", "data": {"month": month}}


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "WORK_SECONDS", 0.0)


def test_a_report_has_one_row_for_each_day_of_its_month() -> None:
    ctx = cast(JobContext, _Ctx())
    assert handle(ctx, _event("2026-10")) == {"month": "2026-10", "rows": 31}
    assert handle(ctx, _event("2026-11")) == {"month": "2026-11", "rows": 30}
    assert handle(ctx, _event("2028-02")) == {"month": "2028-02", "rows": 29}


def test_a_report_logs_its_month_with_the_name_of_its_run() -> None:
    ctx = _Ctx()
    handle(cast(JobContext, ctx), _event("2026-10"))
    assert ctx.lines == ["building the report of 2026-10 in run pyreport-1"]


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "October"])
def test_a_month_that_is_not_yyyy_mm_fails_the_run(month: str) -> None:
    with pytest.raises(ValueError, match=f'month "{month}" is not YYYY-MM'):
        handle(cast(JobContext, _Ctx()), _event(month))


def test_a_runs_preview_answers_what_the_report_would_hold_with_no_work() -> None:
    ctx = _Ctx()
    event = _event("2026-10")
    event["type"] = "funcd.job.preview"
    assert preview(cast(JobContext, ctx), event) == {"month": "2026-10", "rows": 31}
    assert ctx.lines == [], "a preview builds nothing"
    with pytest.raises(ValueError, match='month "October" is not YYYY-MM'):
        preview(cast(JobContext, ctx), _event("October"))
