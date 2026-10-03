"""The example READMEs' `funcdctl apply` commands must work as written. This checks every example,
because s3-lakehouse is CRD-only and has no test project of its own."""

from __future__ import annotations

import re
from pathlib import Path

_EXAMPLES = Path(__file__).resolve().parents[2]


def _apply_commands(readme: Path) -> list[str]:
    return re.findall(r"funcdctl apply\b[^\n]*", readme.read_text().replace("\\\n", " "))


def _applied_files(readme: Path) -> list[str]:
    return [
        target
        for command in _apply_commands(readme)
        for target in re.findall(r"(?:-f|--file)\s+(\S+\.ya?ml)\b", command)
    ]


def test_issue_r32_readmes_apply_one_file_per_command() -> None:
    repeated = [
        f"{readme.relative_to(_EXAMPLES)}: {command}"
        for readme in sorted(_EXAMPLES.glob("*/README.md"))
        for command in _apply_commands(readme)
        if len(re.findall(r"(?:^|\s)(?:-f|--file)\b", command)) > 1
    ]
    assert not repeated, f"funcdctl apply -f takes one file; a repeated -f applies only the last: {repeated}"


def test_issue_r39_readmes_apply_files_the_example_ships() -> None:
    missing = [
        f"{readme.relative_to(_EXAMPLES)}: {target}"
        for readme in sorted(_EXAMPLES.glob("*/README.md"))
        for target in _applied_files(readme)
        if not (readme.parent / target).is_file()
    ]
    assert not missing, f"a README applies a manifest its example does not ship: {missing}"


def test_issue_r39_readmes_name_the_runtime_of_their_funcdctl_yaml() -> None:
    stale = []
    for readme in sorted(_EXAMPLES.glob("*/README.md")):
        example = readme.parent
        declared = {
            runtime
            for manifest in example.glob("*funcdctl.yaml")
            for runtime in re.findall(r"^runtime:\s*(\S+)", manifest.read_text(), re.MULTILINE)
        }
        documents = [readme, *(example / f for f in _applied_files(readme) if (example / f).is_file())]
        stale += [
            f"{document.relative_to(_EXAMPLES)}: {runtime}"
            for document in documents
            for runtime in sorted(set(re.findall(r"\bpython3\d+\b", document.read_text())))
            if declared and runtime not in declared
        ]
    assert not stale, (
        f"a README or the manifest it applies names a runtime its funcdctl.yaml does not: {stale}"
    )
