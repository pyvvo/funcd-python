"""The example READMEs' `funcdctl apply` commands must work as written. This checks every example,
because s3-lakehouse is CRD-only and has no test project of its own."""

from __future__ import annotations

import re
from pathlib import Path

_EXAMPLES = Path(__file__).resolve().parents[2]


def test_issue_r32_readmes_apply_one_file_per_command() -> None:
    repeated = [
        f"{readme.relative_to(_EXAMPLES)}: {command}"
        for readme in sorted(_EXAMPLES.glob("*/README.md"))
        for command in re.findall(r"funcdctl apply\b[^\n]*", readme.read_text().replace("\\\n", " "))
        if len(re.findall(r"(?:^|\s)(?:-f|--file)\b", command)) > 1
    ]
    assert not repeated, f"funcdctl apply -f takes one file; a repeated -f applies only the last: {repeated}"
