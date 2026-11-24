"""``context.job``: a function starts a run of a Job that it binds, and reads it back, over the worker-node
local API (funcd ADR-0349): ``POST /job/{alias}`` and ``GET /job/{alias}/{run}``. funcd checks the binding
and the Policies, and admits the run as a person's. A Job's worker also reads its own run there,
``GET /job/run``, with the approval its handler's context carries (funcd ADR-0348). Stdlib-only,
synchronous."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

from .invoke import MEMBER_HEADER, local_api

if TYPE_CHECKING:
    from .types import Approval, JobRunState


class JobError(RuntimeError):
    """A start or a read of a run that funcd refused: ``status`` is its HTTP status, and ``problem`` funcd's
    problem document (RFC 9457), or ``{"detail": <the body>}`` for a body that is no problem."""

    def __init__(self, call: str, status: int, problem: dict[str, Any]) -> None:
        super().__init__(f"{call} failed: {status} {problem.get('detail', '')}")
        self.status = status
        self.problem = problem


def _request(
    client: str, method: str, path: str, member: str | None, body: bytes | None = None
) -> tuple[int, bytes]:
    """One request on the local API socket: its status and body."""
    conn = local_api()
    if conn is None:
        raise RuntimeError(f"{client}: worker-node local API socket unavailable (FUNCD_INVOKE_SOCKET unset)")
    headers = {MEMBER_HEADER: member} if member else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
    finally:
        conn.close()
    return resp.status, data


def _refusal(call: str, status: int, data: bytes) -> JobError:
    """funcd's problem as a JobError. A body that is no problem, as the 422 of an input off the Job's
    contract, which answers as a Function's call does, keeps its text as the detail, as context.invoke's
    error does."""
    text = data.decode("utf-8", "replace")
    try:
        problem = json.loads(text)
    except ValueError:
        problem = None
    if not isinstance(problem, dict):
        problem = {"detail": text}
    elif "detail" not in problem:
        problem = {**problem, "detail": text}
    return JobError(call, status, problem)


def _parsed(call: str, status: int, data: bytes) -> Any:
    try:
        return json.loads(data)
    except ValueError as err:
        raise RuntimeError(f"{call} failed: {status} reply is not JSON: {err}") from err


class JobClient:
    """The Jobs that a function binds (``spec.bindings.job``), by alias."""

    def __init__(self, member: str | None = None) -> None:
        # In a pool, every request names the calling member; funcd checks it against the pool.
        self._member = member

    def start(self, alias: str, input: Any = None, name: str | None = None) -> str:  # noqa: A002 - the ADR's name
        """Starts a run of the Job that the function binds under ``alias``, with ``input`` as the run's
        input, and returns the run's name at once, without waiting for the run. Without ``name``, funcd names
        the run ``<job>-<id>``, and a start sent again starts a second run; with ``name``, a start sent again
        with the same input returns that run, so a function called more than once for one event, as
        at-least-once delivery does, names the run from the event's id. A run of a Job without
        ``approval.bypass`` waits for a person's approval. Raises ``JobError``: 403 when the function binds
        no Job under ``alias`` or a Policy forbids it, 404 when the Job does not exist, 409 when another run
        holds the name, 413 when the input passes ``job.payloadLimit``, and 422 when it does not match the
        Job's input schema."""
        call = f'context.job.start("{alias}")'
        sent: dict[str, Any] = {"input": input}
        if name is not None:
            sent["name"] = name
        status, data = _request(
            "context.job", "POST", f"/job/{quote(alias, safe='')}", self._member, json.dumps(sent).encode()
        )
        if not 200 <= status < 300:
            raise _refusal(call, status, data)
        return cast("str", _parsed(call, status, data)["run"])

    def get(self, alias: str, run: str) -> JobRunState:
        """Reads ``run``, a run of the Job that the function binds under ``alias``: its phase, its reason,
        its times and, once it Succeeded, its result. Raises ``JobError``: 403 when the function binds no Job
        under ``alias`` or a Policy forbids it, and 404 when the run is not one of that Job's, or funcd
        removed it after ``job.retention``."""
        call = f'context.job.get("{alias}", "{run}")'
        status, data = _request(
            "context.job", "GET", f"/job/{quote(alias, safe='')}/{quote(run, safe='')}", self._member
        )
        if not 200 <= status < 300:
            raise _refusal(call, status, data)
        got = _parsed(call, status, data)
        return {
            "name": got["name"],
            "phase": got["phase"],
            "reason": got.get("reason", ""),
            "started_at": got.get("startedAt"),
            "ended_at": got.get("endedAt"),
            "result": got.get("result"),
        }


def read_approval() -> Approval | None:
    """The approval of the caller's own run, read from funcd (funcd ADR-0348): ``None`` for a run with
    none, as a run of a Job with ``approval.bypass``. Raises when funcd does not answer the run, so a handler
    never runs with an approval it could not read."""
    call = "context.approval"
    status, data = _request(call, "GET", "/job/run", None)
    if status != 200:
        raise _refusal(call, status, data)
    approval = _parsed(call, status, data).get("approval")
    if approval is None:
        return None
    return {
        "approved_by": approval["approvedBy"],
        "digest": approval["digest"],
        "preview": approval["preview"],
    }
