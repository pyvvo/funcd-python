"""Tests for the materialization shape-gate (resolve the handler) — ADR-0049."""

from __future__ import annotations

from pathlib import Path

import pytest

from funcd_shim import runtime


def _artifact(tmp_path: Path, body: str) -> str:
    path = tmp_path / "artifact.py"
    path.write_text(body)
    return str(path)


def test_resolve_handler_ok(tmp_path: Path) -> None:
    module = runtime.load_module(_artifact(tmp_path, "def handle(ctx, event):\n    return {'ok': True}\n"))
    handler = runtime.resolve_handler(module, "handle")
    assert callable(handler)


def test_resolve_handler_missing(tmp_path: Path) -> None:
    module = runtime.load_module(_artifact(tmp_path, "x = 1\n"))
    with pytest.raises(runtime.ShapeError):
        runtime.resolve_handler(module, "handle")


def test_resolve_handler_not_callable(tmp_path: Path) -> None:
    module = runtime.load_module(_artifact(tmp_path, "handle = 42\n"))
    with pytest.raises(runtime.ShapeError):
        runtime.resolve_handler(module, "handle")


def test_load_module_import_error(tmp_path: Path) -> None:
    with pytest.raises(runtime.ShapeError):
        runtime.load_module(_artifact(tmp_path, "def handle(:\n"))  # syntax error
