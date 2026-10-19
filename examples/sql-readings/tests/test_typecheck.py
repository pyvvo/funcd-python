"""Each function's entry passes strict mypy, checked from inside its src/<function>/ directory: every entry is
a main.py, and a function name such as orders-migrate is no package name (funcd ADR-0247)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mypy import api

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("function", sorted(p.name for p in (_ROOT / "src").iterdir() if p.is_dir()))
def test_each_function_passes_strict_mypy(monkeypatch: pytest.MonkeyPatch, function: str) -> None:
    monkeypatch.chdir(_ROOT / "src" / function)
    stdout, stderr, status = api.run(["--config-file", str(_ROOT / "pyproject.toml"), "main.py"])
    assert status == 0, stdout + stderr
