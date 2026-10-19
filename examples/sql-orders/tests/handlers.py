"""Loads a function's entry, src/<function>/main.py, by path: every entry is a main.py (funcd ADR-0247)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_SRC = Path(__file__).resolve().parent.parent / "src"


def load(function: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{function.replace('-', '_')}_main", _SRC / function / "main.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
