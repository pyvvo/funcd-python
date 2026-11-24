"""context.job (funcd ADR-0349): ``start`` sends ``POST /job/<alias>`` and returns the run's name, ``get``
sends ``GET /job/<alias>/<run>``, and funcd's refusal raises a JobError."""

from __future__ import annotations

import json

import pytest
from fakeapi import DependencyAPI

from funcd_shim.job import JobClient, JobError


@pytest.fixture
def api(dependency_api: DependencyAPI, monkeypatch: pytest.MonkeyPatch) -> DependencyAPI:
    monkeypatch.setenv("FUNCD_INVOKE_SOCKET", dependency_api.path)
    return dependency_api


def test_start_sends_the_input_and_the_name_and_returns_the_run(api: DependencyAPI) -> None:
    api.routes["/job/stack"] = (201, b'{"run": "tofu-gen-7"}')
    job = JobClient("planner")
    assert job.start("stack", {"op": "plan"}, "tofu-gen-7") == "tofu-gen-7"
    assert job.start("stack", {"op": "plan"}) == "tofu-gen-7"
    assert api.calls == ["POST /job/stack planner", "POST /job/stack planner"]
    assert [json.loads(b) for b in api.bodies] == [
        {"input": {"op": "plan"}, "name": "tofu-gen-7"},
        {"input": {"op": "plan"}},
    ]


def test_a_start_sent_again_that_funcd_answers_200_returns_the_run(api: DependencyAPI) -> None:
    api.routes["/job/stack"] = (200, b'{"run": "tofu-gen-7"}')
    assert JobClient().start("stack", {}, "tofu-gen-7") == "tofu-gen-7"


def test_get_reads_the_run_in_snake_case(api: DependencyAPI) -> None:
    api.routes["/job/stack/tofu-1"] = (
        200,
        json.dumps(
            {
                "name": "tofu-1",
                "phase": "Succeeded",
                "reason": "",
                "startedAt": "2026-11-24T09:00:00Z",
                "endedAt": "2026-11-24T09:00:03Z",
                "result": {"applied": 2},
            }
        ).encode(),
    )
    api.routes["/job/stack/tofu-2"] = (200, b'{"name": "tofu-2", "phase": "Pending", "reason": "Queued"}')
    job = JobClient()
    assert job.get("stack", "tofu-1") == {
        "name": "tofu-1",
        "phase": "Succeeded",
        "reason": "",
        "started_at": "2026-11-24T09:00:00Z",
        "ended_at": "2026-11-24T09:00:03Z",
        "result": {"applied": 2},
    }
    assert job.get("stack", "tofu-2") == {
        "name": "tofu-2",
        "phase": "Pending",
        "reason": "Queued",
        "started_at": None,
        "ended_at": None,
        "result": None,
    }
    assert api.calls == ["GET /job/stack/tofu-1 -", "GET /job/stack/tofu-2 -"]


def test_a_refusal_raises_a_job_error_with_its_problem(api: DependencyAPI) -> None:
    api.routes["/job/stack"] = (409, b'{"title": "Conflict", "status": 409, "detail": "taken"}')
    api.routes["/job/other"] = (403, b"denied")
    with pytest.raises(JobError, match=r'context.job.start\("stack"\) failed: 409 taken') as err:
        JobClient().start("stack", {}, "tofu-gen-7")
    assert err.value.status == 409
    assert err.value.problem["detail"] == "taken"
    with pytest.raises(JobError) as err:
        JobClient().start("other", {})
    assert err.value.problem == {"detail": "denied"}
    assert isinstance(err.value, RuntimeError), "a JobError is the shim's error, as context.addon's"


def test_an_input_off_the_contract_raises_422_with_the_body_as_its_detail(api: DependencyAPI) -> None:
    violation = (
        b'{"error": "run input does not match the input schema of Job report",'
        b' "details": [{"path": "/month", "message": "required"}]}'
    )
    api.routes["/job/stack"] = (422, violation)
    with pytest.raises(JobError, match=r"failed: 422 \{\"error\": \"run input does not match") as err:
        JobClient().start("stack", {})
    assert err.value.status == 422
    assert err.value.problem["detail"] == violation.decode()
    assert err.value.problem["details"] == [{"path": "/month", "message": "required"}]
