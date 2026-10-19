"""Build a funcd Python bundle (funcd ADR-0144, ADR-0247).

A bundle is a directory `funcdctl push <dir> <ref>` turns into one OCI layer (ADR-0089): the handler, the
exact locked versions of its runtime dependencies installed for the runtime's Linux platform, and the
function's contract file ``<name>.contract.yaml`` (or ``.json``) at its root. The runtime ships ``funcd_shim``
and its dependencies, so they are pruned.

Every container step copies its inputs in and its outputs out as a tar stream (``docker cp``) and binds no
host path, so it also works from a job container that drives another Docker daemon.
"""

from __future__ import annotations

import ast
import csv
import io
import os
import platform as host
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

BASE_IMAGE = "python:3.14-slim-bookworm"
# BASE_IMAGE pinned per platform (its index is sha256:48b13b00…0f83), so a bundle builds from one image on
# every machine. Not the index digest: the classic image store maps one digest to one image, so it cannot hold
# both platforms of an index under one digest. Move the three together.
BASE_IMAGES = {
    "linux/amd64": "python@sha256:a66d3a463d0be3cc537c269b0d3418746dda512417a975559a0eaa8a6406baaf",
    "linux/arm64": "python@sha256:0e24db28cf5b7788413afe3fdc63075ca22bc4ecf74f5472304b391c2110e629",
}
PYTHON_VERSION = "3.14"
GLIBC = "2_36"
CONTRACT_SUFFIXES = (".contract.yaml", ".contract.json")
MANIFEST_SUFFIX = ".funcdctl.yaml"
GENERIC_MANIFEST = "funcdctl.yaml"
# a project without [tool.funcd-bundle.functions] is one function, named after its directory, from this entry
DEFAULT_ENTRY = "src/main.py"
# funcdctl push's --entry default for a python bundle (funcd ADR-0247 Decision 2)
PUSH_ENTRY = "main.py"
# The runtime provides the shim and everything only it needs (ADR-0071: fastjsonschema).
RUNTIME_PACKAGE = "funcd-shim"
PLATFORMS = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}
SKIPPED = {"__pycache__", "tests"}
# never streamed into a container: caches, virtualenvs and build outputs
NOT_COPIED = frozenset(
    {".venv", ".git", "node_modules", "__pycache__", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)


class BundleError(Exception):
    """A step failed; the message names the package, file or command."""


@dataclass(frozen=True)
class Function:
    name: str
    contract: Path
    handler: Path


@dataclass(frozen=True)
class Settings:
    hermetic: bool
    post_install: list[str]


def host_platform() -> str:
    """linux/<host arch>: the platform a bundle targets by default."""
    machine = host.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "linux/arm64"
    if machine in ("x86_64", "amd64"):
        return "linux/amd64"
    raise BundleError(f"unsupported host architecture {machine!r}; pass --platform")


def discover(project: Path) -> dict[str, Function]:
    """The functions [tool.funcd-bundle.functions] lists (name → entry, relative to the project root), else
    one named after the project directory, from src/main.py. Each needs its contract file <name>.contract.yaml
    or .json at the project root; a funcdctl.yaml is refused, since nothing reads it (funcd ADR-0247)."""
    old = sorted(project.glob("*" + MANIFEST_SUFFIX)) + [
        p for p in [project / GENERIC_MANIFEST] if p.is_file()
    ]
    if old:
        names = ", ".join(str(p) for p in old)
        raise BundleError(
            f"{names}: funcdctl.yaml is no longer read (funcd ADR-0247): put each function's runtime and "
            f"contract in <name>.contract.yaml at {project}, its bindings in its Function resource file, and "
            "its entry in [tool.funcd-bundle.functions]"
        )
    entries = _functions_table(project)
    if entries is None:
        entries = {project.resolve().name: DEFAULT_ENTRY}
    functions = {}
    for name, entry in entries.items():
        handler = project / entry
        if not handler.is_file():
            raise BundleError(f"function {name}: entry {handler} does not exist")
        functions[name] = Function(name=name, contract=_contract(project, name), handler=handler)
    return functions


def _functions_table(project: Path) -> dict[str, str] | None:
    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        table = tomllib.loads(pyproject.read_text()).get("tool", {}).get("funcd-bundle", {}).get("functions")
    except tomllib.TOMLDecodeError as err:
        raise BundleError(f"{pyproject}: {err}") from err
    if table is None:
        return None
    if not isinstance(table, dict) or not table or not all(isinstance(v, str) for v in table.values()):
        raise BundleError(
            f"{pyproject}: [tool.funcd-bundle.functions] must map each function name to its entry, a path"
        )
    return table


def _contract(project: Path, name: str) -> Path:
    paths = [project / (name + suffix) for suffix in CONTRACT_SUFFIXES]
    found = [p for p in paths if p.is_file()]
    if not found:
        raise BundleError(f"function {name} has no contract file: neither {paths[0]} nor {paths[1]} exists")
    if len(found) > 1:
        raise BundleError(f"function {name} has two contract files, {paths[0]} and {paths[1]}: keep one")
    return found[0]


def settings(project: Path) -> Settings:
    pyproject = project / "pyproject.toml"
    table: dict[str, object] = {}
    if pyproject.is_file():
        try:
            tool = tomllib.loads(pyproject.read_text()).get("tool", {})
        except tomllib.TOMLDecodeError as err:
            raise BundleError(f"{pyproject}: {err}") from err
        table = tool.get("funcd-bundle", {})
    hermetic = table.get("hermetic", False)
    post_install = table.get("post-install", [])
    if not isinstance(hermetic, bool):
        raise BundleError("[tool.funcd-bundle] hermetic must be true or false")
    if not isinstance(post_install, list) or not all(isinstance(a, str) for a in post_install):
        raise BundleError("[tool.funcd-bundle] post-install must be a list of strings (an argv)")
    return Settings(hermetic=hermetic, post_install=post_install)


def bundle(project: Path, name: str, platform: str, out: Path, *, hermetic: bool, check: bool) -> Path:
    """Build function `name` of `project` for `platform` into `out` (replaced) and return it."""
    if platform not in PLATFORMS:
        raise BundleError(f"unsupported platform {platform!r}; use one of {', '.join(PLATFORMS)}")
    functions = discover(project)
    if name not in functions:
        raise BundleError(f"no function {name!r} in {project}; found {', '.join(functions)}")
    fn = functions[name]
    conf = settings(project)
    if conf.post_install and not hermetic:
        raise BundleError("[tool.funcd-bundle] post-install runs only in a hermetic build (--hermetic)")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="funcd-bundle-") as tmp:
        work = Path(tmp)
        registry, members = _export(project)
        wheels = [_build_wheel(member, work / "wheels") for member in members]
        if hermetic:
            _install_in_container(project, platform, registry, wheels, conf.post_install, out, work)
        else:
            _install_on_host(platform, registry, wheels, out, work)
    vendored = _top_level_modules(out)
    sources = _copy_handler(project, fn, out)
    if check:
        needed = sorted(_imports(sources) & vendored)
        if needed:
            _check(out, platform, needed)
    return out


def _uv() -> str:
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise BundleError("uv not found: run funcd-bundle with `uv run funcd-bundle`")
    return uv


def _run(argv: list[str], *, cwd: Path | None = None, stdin: bytes | None = None) -> bytes:
    try:
        done = subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True, check=False)  # noqa: S603
    except FileNotFoundError as err:
        raise BundleError(f"{argv[0]} not found") from err
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).decode(errors="replace").strip()
        raise BundleError(f"`{shlex.join(argv)}` failed:\n{detail}")
    return done.stdout


def _export(project: Path) -> tuple[str, list[Path]]:
    """The locked runtime closure: hashed registry requirements, and the workspace members it includes."""
    text = _run(
        [
            _uv(),
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-editable",
            "--prune",
            RUNTIME_PACKAGE,
            "--no-header",
            "--no-annotate",
        ],
        cwd=project,
    ).decode()
    root = Path(_run([_uv(), "workspace", "dir"], cwd=project).decode().strip())
    registry: list[str] = []
    members: list[Path] = []
    for entry in _requirements(text):
        if entry.startswith(("./", "../", "/")):
            members.append((root / entry).resolve())
        else:
            registry.append(entry)
    return "\n".join(registry) + ("\n" if registry else ""), members


def _requirements(text: str) -> list[str]:
    """Join `uv export` continuation lines into one entry per requirement."""
    entries: list[str] = []
    current = ""
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        current += line.rstrip("\\").strip() + " " if line.endswith("\\") else line.strip()
        if not line.endswith("\\"):
            entries.append(current.strip())
            current = ""
    return entries


def _build_wheel(member: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    before = set(dest.glob("*.whl"))
    _run([_uv(), "build", "--wheel", "--quiet", "--out-dir", str(dest), str(member)])
    built = sorted(set(dest.glob("*.whl")) - before)
    if len(built) != 1:
        raise BundleError(f"building workspace member {member.name} produced {len(built)} wheels")
    if not built[0].name.endswith("-none-any.whl"):
        raise BundleError(f"workspace member {member.name} is not pure Python ({built[0].name})")
    return built[0]


def _install_on_host(platform: str, registry: str, wheels: list[Path], out: Path, work: Path) -> None:
    target = [
        "--target",
        str(out),
        "--no-deps",
        "--only-binary",
        ":all:",
        "--python-version",
        PYTHON_VERSION,
        "--python-platform",
        f"{PLATFORMS[platform]}-manylinux_{GLIBC}",
    ]
    if registry:
        reqs = work / "requirements.txt"
        reqs.write_text(registry)
        _run([_uv(), "pip", "install", "--quiet", *target, "--require-hashes", "-r", str(reqs)])
    if wheels:
        _run([_uv(), "pip", "install", "--quiet", *target, *map(str, wheels)])


def _install_in_container(
    project: Path,
    platform: str,
    registry: str,
    wheels: list[Path],
    post_install: list[str],
    out: Path,
    work: Path,
) -> None:
    pip = (
        "pip install --quiet --no-cache-dir --root-user-action=ignore"
        " --target /out --no-deps --only-binary :all:"
    )
    steps = ["set -e", "mkdir -p /out"]
    if registry:
        steps.append(f"{pip} --require-hashes -r /in/requirements.txt")
    if wheels:
        steps.append(f"{pip} /in/wheels/*.whl")
    if post_install:
        steps.append(shlex.join(post_install))
    inputs = work / "in"
    (inputs / "wheels").mkdir(parents=True)
    (inputs / "requirements.txt").write_text(registry)
    for wheel in wheels:
        shutil.copy2(wheel, inputs / "wheels" / wheel.name)
    env = ["-e", "FUNCD_BUNDLE_DIR=/out", "-e", "PYTHONPATH=/out", "-w", "/src"]
    cid = _create(platform, env, ["sh", "-c", "; ".join(steps)])
    try:
        _copy_in(cid, inputs, "/in", NOT_COPIED)
        _copy_in(cid, project, "/src", NOT_COPIED)
        _start(cid, "hermetic install")
        _copy_out(cid, "/out", out)
    finally:
        _run(["docker", "rm", "-f", cid])


def _check(out: Path, platform: str, modules: list[str]) -> None:
    """Import the vendored modules the handler uses, in the runtime's base image for the target platform."""
    code = "; ".join(f"import {m}" for m in modules)
    cid = _create(platform, ["-e", "PYTHONPATH=/bundle"], ["python", "-c", code])
    try:
        # a vendored package may hold a dist or node_modules directory of its own: check the tree as built
        _copy_in(cid, out, "/bundle", frozenset({"__pycache__"}))
        _start(cid, f"import check ({', '.join(modules)}) on {platform}")
    finally:
        _run(["docker", "rm", "-f", cid])


def _create(platform: str, env: list[str], command: list[str]) -> str:
    return (
        _run(["docker", "create", "--platform", platform, *env, BASE_IMAGES[platform], *command])
        .decode()
        .strip()
    )


def _start(cid: str, what: str) -> None:
    done = subprocess.run(["docker", "start", "-a", cid], capture_output=True, check=False)  # noqa: S603, S607
    if done.returncode != 0:
        detail = (done.stderr + done.stdout).decode(errors="replace").strip()
        raise BundleError(f"{what} failed:\n{detail}")


def _copy_in(cid: str, src: Path, dest: str, skip: frozenset[str]) -> None:
    """Stream src into the container at dest as a tar (no bind mount), leaving out the names in skip."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(src, arcname=dest.lstrip("/"), filter=lambda ti: None if Path(ti.name).name in skip else ti)
    _run(["docker", "cp", "-", f"{cid}:/"], stdin=buf.getvalue())


def _copy_out(cid: str, src: str, dest: Path) -> None:
    data = _run(["docker", "cp", f"{cid}:{src}/.", "-"])
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            rel = Path(member.name)
            parts = rel.parts[1:] if rel.parts and rel.parts[0] in (".", Path(src).name) else rel.parts
            if not parts:
                continue
            member.name = str(Path(*parts))
            tar.extract(member, dest, filter="data")


def _copy_handler(project: Path, fn: Function, out: Path) -> list[Path]:
    """The handler file alone when it sits at the project root, else its directory tree; then the contract
    file, at the bundle's root, where funcdctl push reads it. Returns the copied Python sources."""
    source = fn.handler.parent
    if source.resolve() == project.resolve():
        shutil.copy2(fn.handler, out / fn.handler.name)
        sources = [fn.handler]
    else:

        def ignore(_dir: str, names: list[str]) -> list[str]:
            return [n for n in names if n in SKIPPED or n.startswith(".") or n.endswith(".pyc")]

        shutil.copytree(source, out, dirs_exist_ok=True, ignore=ignore)
        sources = [p for p in source.rglob("*.py") if not SKIPPED.intersection(p.relative_to(source).parts)]
    shutil.copy2(fn.contract, out / fn.contract.name)
    return sources


def _top_level_modules(out: Path) -> set[str]:
    """The importable top-level names of the vendored distributions, from each RECORD."""
    names: set[str] = set()
    for record in out.glob("*.dist-info/RECORD"):
        with record.open(newline="") as fh:
            for row in csv.reader(fh):
                if not row:
                    continue
                first = Path(row[0]).parts[0]
                if first.endswith((".dist-info", ".data", ".libs")) or first in ("bin", "__pycache__", ".."):
                    continue
                if not (row[0].endswith((".py", ".so", ".pyd")) or "/" in row[0]):
                    continue
                name = first.split(".")[0]
                if name.isidentifier():
                    names.add(name)
    return names


def _imports(sources: list[Path]) -> set[str]:
    """The absolute top-level modules imported by the handler's sources."""
    names: set[str] = set()
    for src in sources:
        try:
            tree = ast.parse(src.read_text(), filename=str(src))
        except SyntaxError as err:
            raise BundleError(f"{src.name}: {err}") from err
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names
