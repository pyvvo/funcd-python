"""Fixtures shared by the shim tests."""

import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def sock_dir() -> Iterator[Path]:
    """A short directory to bind Unix sockets in, independent of TMPDIR: an AF_UNIX path is limited to
    104 bytes on macOS (108 on Linux), and pytest's tmp_path under a long TMPDIR overruns it."""
    path = Path(tempfile.mkdtemp(prefix="fs", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
