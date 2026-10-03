"""The shim and its tests import at module top level: no module has a circular import to break."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import funcd_shim

PACKAGE = Path(funcd_shim.__file__).parent
TESTS = Path(__file__).parent


def _imports_inside_functions(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            lines += [n.lineno for n in ast.walk(node) if isinstance(n, ast.Import | ast.ImportFrom)]
    return sorted(set(lines))


@pytest.mark.parametrize("module", sorted(p.name for p in PACKAGE.glob("*.py")))
def test_issue_r28_shim_modules_import_at_top_level(module: str) -> None:
    lines = _imports_inside_functions((PACKAGE / module).read_text(encoding="utf-8"))
    assert lines == [], f"{module} imports inside a function at lines {lines}"


@pytest.mark.parametrize("module", sorted(p.name for p in TESTS.glob("*.py")))
def test_issue_r45_shim_tests_import_at_top_level(module: str) -> None:
    lines = _imports_inside_functions((TESTS / module).read_text(encoding="utf-8"))
    assert lines == [], f"tests/{module} imports inside a function at lines {lines}"
