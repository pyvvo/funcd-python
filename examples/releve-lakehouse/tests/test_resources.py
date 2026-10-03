"""The deploy manifests under resources/ must be what `funcdctl apply` accepts: funcd's v1alpha1 shapes
(api/types/v1alpha1 in pyvvo/funcd, decoded strictly), applied one file at a time."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from funcd_shim.contract import load_from_path

_ROOT = Path(__file__).resolve().parent.parent
_REPO = _ROOT.parent.parent
_ENVELOPE = {"apiVersion", "kind", "metadata", "spec"}
# funcd's WorkflowStep fields: a step's static input overlay is `params`, there is no `input`.
_STEP_FIELDS = {"name", "function", "builtin", "workflow", "dependsOn", "join", "when", "params"}
# A blob EventSource's `arrived` CloudEvent (funcd ADR-0119): data is {bucket, key, size, version, time}.
_ARRIVED: dict[str, Any] = {
    "type": "arrived",
    "data": {
        "bucket": "releves",
        "key": "landing/synthetic-releve-2025-11.pdf",
        "size": 2048,
        "version": "v1",
        "time": "2025-11-30T00:00:00Z",
    },
}
_EVENT_EXPR = re.compile(r"^\$\{\{\s*event\.([\w.]+)\s*\}\}$")


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


def _sensor_run_input(fields: dict[str, Any] | None) -> Any:
    """The run input a Sensor action builds (funcd ADR-0109): no `input` is the event data verbatim, else
    each field is a literal or a `${{ event.<path> }}` projection over the CloudEvent."""
    if fields is None:
        return _ARRIVED["data"]
    out: dict[str, Any] = {}
    for name, value in fields.items():
        match = _EVENT_EXPR.match(value) if isinstance(value, str) else None
        node: Any = value
        if match is not None:
            node = _ARRIVED
            for part in match.group(1).split("."):
                node = node[part]
        out[name] = node
    return out


def test_issue_r33_root_step_accepts_every_run_input(tmp_path: Path) -> None:
    run_inputs: list[tuple[str, Any]] = [("workflow run without --input", None)]
    readme = (_ROOT / "README.md").read_text()
    for raw in re.findall(r"funcdctl workflow run releve-pipeline [^\n]*?--input '([^']*)'", readme):
        run_inputs.append((f"README --input {raw}", json.loads(raw)))
    workflows = {}
    for name, doc in _resources():
        if doc["kind"] == "Workflow":
            workflows[doc["metadata"]["name"]] = doc
        if doc["kind"] == "Sensor":
            for action in doc["spec"]["do"]:
                if action.get("workflow") == "releve-pipeline":
                    source = f"{name}: action {action['name']}"
                    run_inputs.append((source, _sensor_run_input(action.get("input"))))
    assert any(source.startswith("sensor.yaml") for source, _ in run_inputs), "no Sensor starts the pipeline"

    problems = []
    for step in workflows["releve-pipeline"]["spec"]["steps"]:
        if step.get("dependsOn"):
            continue
        ref = step["function"]["ref"]
        contract = tmp_path / f"{ref}.json"
        contract.write_text(
            json.dumps(yaml.safe_load((_ROOT / f"{ref}.funcdctl.yaml").read_text())["contract"])
        )
        validate = load_from_path(str(contract)).input
        assert validate is not None
        problems += [
            f"{ref} rejects {src}: {errs}" for src, run_input in run_inputs if (errs := validate(run_input))
        ]
    assert not problems


def test_issue_r17_readme_applies_one_file_at_a_time() -> None:
    targets = re.findall(r"funcdctl apply -f (\S+)", (_ROOT / "README.md").read_text())
    assert targets, "the README must say how to apply resources/"
    for target in targets:
        assert not (_ROOT / target).is_dir(), f"funcdctl apply -f takes a file, not the directory {target}"


def test_issue_r35_resources_name_only_existing_files() -> None:
    missing = [
        f"{path.name} names {ref}"
        for path in sorted((_ROOT / "resources").glob("*.yaml"))
        for ref in re.findall(r"[\w./-]+\.yaml\b", path.read_text())
        if not any((base / ref).exists() for base in (path.parent, _ROOT, _REPO))
    ]
    assert not missing


def _words(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _words(key)
            yield from _words(value)
    elif isinstance(node, list):
        for item in node:
            yield from _words(item)
    elif isinstance(node, str):
        yield from re.findall(r"\w+", node)


def _shipped_names() -> set[str]:
    """Every name the example's code, manifests and file layout use, comments and docstrings left out."""
    names: set[str] = set()
    for path in _ROOT.rglob("*"):
        rel = path.relative_to(_ROOT)
        if rel.parts[0] == "tests" or any(p == "bundle" or p.startswith(".") for p in rel.parts):
            continue
        names.update(_words(path.name))
        if path.suffix == ".py":
            tree = ast.parse(path.read_text())
            docstrings = {
                ast.get_docstring(node, clean=False)
                for node in ast.walk(tree)
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.alias):
                    names.update(node.name.split("."))
                elif isinstance(node, ast.Constant) and node.value not in docstrings:
                    names.update(_words(node.value))
        elif path.suffix == ".yaml":
            for doc in yaml.safe_load_all(path.read_text()):
                names.update(_words(doc))
        elif path.suffix == ".toml":
            names.update(_words(tomllib.loads(path.read_text())))
        elif path.suffix == ".sql":
            names.update(_words(path.read_text()))
    return names


def test_issue_r42_docs_name_only_modules_the_example_has() -> None:
    shipped = _shipped_names()
    stale = []
    docs = [
        _ROOT / "README.md",
        _ROOT / "landing" / "README.md",
        _ROOT / "pyproject.toml",
        *_ROOT.glob("*.yaml"),
    ]
    for path in docs:
        text = path.read_text()
        names = re.findall(r"`import (\w+)", text)
        if path.suffix == ".md":
            names += re.findall(r"`([a-z_][a-z0-9_]*)`", text)
        stale += [f"{path.relative_to(_ROOT)} names {name}" for name in names if name not in shipped]
    assert not stale
