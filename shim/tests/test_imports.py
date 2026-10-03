"""The shim and its tests import at module top level: no module has a circular import to break.
What calls the shim, or one of its modules, stdlib-only must hold for what it imports and declares."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

import funcd_shim

PACKAGE = Path(funcd_shim.__file__).parent
TESTS = Path(__file__).parent
_REPO = Path(__file__).resolve().parents[2]
_STDLIB_ONLY = re.compile(r"stdlib[- ]only", re.IGNORECASE)


def _imports_inside_functions(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            lines += [n.lineno for n in ast.walk(node) if isinstance(n, ast.Import | ast.ImportFrom)]
    return sorted(set(lines))


def _parse(module: str) -> ast.Module:
    return ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))


def _third_party(module: str) -> set[str]:
    """The non-stdlib top-level modules *module* imports, itself or through the package modules it
    imports (importing any of them runs ``__init__`` first)."""
    found: set[str] = set()
    seen: set[str] = set()
    todo = [module, "__init__"]
    while todo:
        name = todo.pop()
        if name in seen or not (PACKAGE / f"{name}.py").is_file():
            continue
        seen.add(name)
        for node in ast.walk(_parse(name)):
            if isinstance(node, ast.Import):
                found |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                parts = (node.module or "").split(".")
                if node.level == 0 and parts[0] != "funcd_shim":
                    found.add(parts[0])
                    continue
                local = parts[1:] if node.level == 0 else [p for p in parts if p]
                todo += local[:1] or [alias.name for alias in node.names]
    return found - set(sys.stdlib_module_names) - {"funcd_shim"}


@pytest.mark.parametrize("module", sorted(p.name for p in PACKAGE.glob("*.py")))
def test_issue_r28_shim_modules_import_at_top_level(module: str) -> None:
    lines = _imports_inside_functions((PACKAGE / module).read_text(encoding="utf-8"))
    assert lines == [], f"{module} imports inside a function at lines {lines}"


@pytest.mark.parametrize("module", sorted(p.name for p in TESTS.glob("*.py")))
def test_issue_r45_shim_tests_import_at_top_level(module: str) -> None:
    lines = _imports_inside_functions((TESTS / module).read_text(encoding="utf-8"))
    assert lines == [], f"tests/{module} imports inside a function at lines {lines}"


def test_issue_r47_stdlib_only_modules_import_no_third_party_module() -> None:
    wrong = {
        path.name: sorted(loads)
        for path in sorted(PACKAGE.glob("*.py"))
        if _STDLIB_ONLY.search(ast.get_docstring(_parse(path.stem)) or "")
        and (loads := _third_party(path.stem))
    }
    assert not wrong, f"these modules call themselves stdlib-only but import third-party modules: {wrong}"


def test_issue_r47_docs_do_not_call_the_shim_stdlib_only() -> None:
    pyproject = tomllib.loads((_REPO / "shim" / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    claims = [
        f"{doc}: {line.strip()}"
        for doc in ("README.md", "CLAUDE.md")
        for line in (_REPO / doc).read_text(encoding="utf-8").splitlines()
        if "`shim/`" in line and _STDLIB_ONLY.search(line)
    ]
    assert not (dependencies and claims), (
        f"shim/pyproject.toml declares {dependencies}, yet these call the shim stdlib-only: {claims}"
    )
