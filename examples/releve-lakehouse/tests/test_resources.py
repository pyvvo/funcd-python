"""The deploy manifests under resources/ must be what `funcdctl apply` accepts: funcd's v1alpha1 shapes
(api/types/v1alpha1 in pyvvo/funcd, decoded strictly), applied one file at a time."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_ENVELOPE = {"apiVersion", "kind", "metadata", "spec"}
# funcd's WorkflowStep fields: a step's static input overlay is `params`, there is no `input`.
_STEP_FIELDS = {"name", "function", "builtin", "workflow", "dependsOn", "join", "when", "params"}


def _resources() -> list[tuple[str, dict[str, Any]]]:
    return [
        (path.name, doc)
        for path in sorted((_ROOT / "resources").glob("*.yaml"))
        for doc in yaml.safe_load_all(path.read_text())
        if doc is not None
    ]


def test_issue_r17_resources_use_funcd_fields() -> None:
    problems = []
    for name, doc in _resources():
        kind = doc["kind"]
        if extra := set(doc) - _ENVELOPE:
            problems.append(f"{name}: {kind} has unknown top-level fields {sorted(extra)}")
        if kind == "Workflow":
            for step in doc["spec"]["steps"]:
                if extra := set(step) - _STEP_FIELDS:
                    problems.append(f"{name}: step {step['name']} has unknown fields {sorted(extra)}")
        if kind == "EgressPolicy" and not doc["spec"].get("rules"):
            problems.append(f"{name}: spec.rules must list at least one rule")
    assert not problems


def test_issue_r17_readme_applies_one_file_at_a_time() -> None:
    targets = re.findall(r"funcdctl apply -f (\S+)", (_ROOT / "README.md").read_text())
    assert targets, "the README must say how to apply resources/"
    for target in targets:
        assert not (_ROOT / target).is_dir(), f"funcdctl apply -f takes a file, not the directory {target}"
