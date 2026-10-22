"""`just check` must run every example's tests. This lives in an example that is already checked, because an
example the justfile leaves out would never run a test that guards itself."""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def test_issue_r41_just_check_tests_every_example() -> None:
    projects = subprocess.run(
        ["just", "--evaluate", "projects"], cwd=_REPO, check=True, capture_output=True, text=True
    ).stdout.split()
    untested = [
        example
        for tests in sorted(_REPO.glob("examples/*/tests"))
        if (example := str(tests.parent.relative_to(_REPO))) not in projects
    ]
    assert not untested, f"just check runs pytest over `projects`, which leaves out {untested}"
