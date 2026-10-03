"""The example's strict mypy config (pyproject.toml [tool.mypy]) must pass, the way `uv run mypy` runs it."""

from __future__ import annotations

from pathlib import Path

import pytest
from mypy import api

_ROOT = Path(__file__).resolve().parent.parent


def test_issue_r51_strict_mypy_config_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(_ROOT)
    stdout, stderr, status = api.run([])
    assert status == 0, stdout + stderr
