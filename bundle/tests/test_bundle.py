"""Scenario tests for funcd-bundle (funcd ADR-0144).

They build a small uv workspace and lock it, so they need the network; the container cases also need Docker.
Each skips with the reason when its requirement is absent. CI has both.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from funcd_bundle import BundleError, bundle, discover, host_platform
from funcd_bundle.bundle import _check
from funcd_bundle.cli import main

FOREIGN = {"linux/arm64": "linux/amd64", "linux/amd64": "linux/arm64"}
ELF_MACHINE = {"linux/amd64": 0x3E, "linux/arm64": 0xB7}
MANIFEST = "runtime: python314\nhandler: handle\nmain: src/handler.py\n"


def _online() -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=5).close()
    except OSError:
        return False
    return True


def _docker() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


needs_network = pytest.mark.skipif(not _online(), reason="needs pypi.org to lock and install the fixture")
needs_docker = pytest.mark.skipif(not _docker(), reason="needs a running Docker daemon")


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def _uv(*args: str, cwd: Path) -> None:
    uv = os.environ.get("UV") or shutil.which("uv") or "uv"
    subprocess.run([uv, *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A uv workspace with one function (reader) and one pure-Python member (py-common), locked."""
    if not _online():
        pytest.skip("needs pypi.org to lock the fixture")
    root = tmp_path_factory.mktemp("ws")
    _write(
        root,
        {
            "pyproject.toml": (
                '[project]\nname = "apps"\nversion = "0.0.0"\nrequires-python = ">=3.12"\n\n'
                '[tool.uv.workspace]\nmembers = ["functions/*", "packages/*"]\n'
            ),
            "packages/py-common/pyproject.toml": (
                '[project]\nname = "py-common"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
                'dependencies = ["six==1.17.0"]\n\n'
                '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n'
                '[tool.hatch.build.targets.wheel]\npackages = ["src/py_common"]\n'
            ),
            "packages/py-common/src/py_common/__init__.py": 'def hello() -> str:\n    return "common"\n',
            "functions/reader/pyproject.toml": (
                '[project]\nname = "reader"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
                'dependencies = ["funcd-shim>=0.2.0", "orjson==3.11.3", "py-common"]\n\n'
                '[dependency-groups]\ndev = ["iniconfig>=2"]\n\n'
                "[tool.uv.sources]\npy-common = { workspace = true }\n"
            ),
            "functions/reader/funcdctl.yaml": MANIFEST,
            "functions/reader/src/handler.py": (
                "import orjson\nimport py_common\n\n\n"
                "def handle(ctx, event):\n    return orjson.loads(orjson.dumps({'c': py_common.hello()}))\n"
            ),
            "functions/reader/src/tests/test_handler.py": "def test_nothing():\n    pass\n",
        },
    )
    _uv("lock", "--python", "3.14", cwd=root)
    yield root / "functions" / "reader"


def _locked(project: Path, name: str) -> str:
    lock = tomllib.loads((project.parent.parent / "uv.lock").read_text())
    return next(str(p["version"]) for p in lock["package"] if p["name"] == name)


# scenario: bundle-vendors-locked-deps
@needs_network
def test_scenario_bundle_vendors_locked_deps(workspace: Path, tmp_path: Path) -> None:
    out = bundle(workspace, "reader", host_platform(), tmp_path / "reader", hermetic=False, check=False)

    assert (out / "handler.py").is_file()
    assert (out / "funcdctl.yaml").read_text() == MANIFEST.replace("main: src/handler.py", "main: handler.py")
    assert not (out / "tests").exists(), "the handler's tests are not bundled"
    assert (out / f"orjson-{_locked(workspace, 'orjson')}.dist-info").is_dir()
    assert (out / f"six-{_locked(workspace, 'six')}.dist-info").is_dir()
    for absent in ("funcd_shim", "fastjsonschema", "iniconfig"):
        assert not any(out.glob(f"{absent}*")), f"{absent} is provided by the runtime or is a dev dependency"


# scenario: bundle-cross-platform
@needs_network
def test_scenario_bundle_cross_platform(workspace: Path, tmp_path: Path) -> None:
    target = FOREIGN[host_platform()]
    out = bundle(workspace, "reader", target, tmp_path / "reader", hermetic=False, check=False)

    [so] = list((out / "orjson").glob("*.so"))
    assert so.read_bytes()[18] == ELF_MACHINE[target], f"{so.name} is not built for {target}"
    [wheel] = list(out.glob("orjson-*.dist-info/WHEEL"))
    tags = [line.split(": ", 1)[1] for line in wheel.read_text().splitlines() if line.startswith("Tag: ")]
    for tag in tags:
        glibc = tag.split("-")[-1].split("_")
        assert glibc[0] == "manylinux", tag
        if glibc[1].isdigit() and len(glibc) > 3:
            assert (int(glibc[1]), int(glibc[2])) <= (2, 36), f"{tag} needs a glibc newer than the runtime's"


@needs_network
@needs_docker
def test_scenario_bundle_cross_platform_import_check(workspace: Path, tmp_path: Path) -> None:
    target = FOREIGN[host_platform()]
    bundle(workspace, "reader", target, tmp_path / "reader", hermetic=False, check=True)


# scenario: bundle-refuses-source-builds
@needs_network
def test_scenario_bundle_refuses_source_builds(tmp_path: Path) -> None:
    project = tmp_path / "old"
    _write(
        project,
        {
            # PyYAML 5.4.1 has no CPython 3.14 wheel, only its sdist
            "pyproject.toml": (
                '[project]\nname = "old"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
                'dependencies = ["pyyaml==5.4.1"]\n'
            ),
            "funcdctl.yaml": MANIFEST,
            "src/handler.py": "import yaml\n",
        },
    )
    _uv("lock", "--python", "3.14", cwd=project)
    with pytest.raises(BundleError, match="(?i)pyyaml"):
        bundle(project, "old", host_platform(), tmp_path / "out", hermetic=False, check=False)


# scenario: bundle-includes-workspace-package
@needs_network
def test_scenario_bundle_includes_workspace_package(workspace: Path, tmp_path: Path) -> None:
    out = bundle(workspace, "reader", host_platform(), tmp_path / "reader", hermetic=False, check=False)

    assert (out / "py_common" / "__init__.py").is_file(), "installed as files"
    assert not list(out.glob("*.pth")), "not an editable link"
    [direct] = list(out.glob("py_common-*.dist-info"))
    assert (
        not (direct / "direct_url.json").exists()
        or '"editable": true' not in (direct / "direct_url.json").read_text()
    )


# scenario: bundle-check-catches-mismatch
@needs_network
@needs_docker
def test_scenario_bundle_check_catches_mismatch(workspace: Path, tmp_path: Path) -> None:
    foreign = FOREIGN[host_platform()]
    out = bundle(workspace, "reader", foreign, tmp_path / "reader", hermetic=False, check=False)
    with pytest.raises(BundleError, match="import check") as err:
        _check(out, host_platform(), ["orjson"])
    detail = str(err.value).split("\n", 1)[1]
    assert re.search("ModuleNotFoundError|ImportError", detail), (
        f"the import error itself is reported: {detail}"
    )


# scenario: bundle-hermetic-post-install
@needs_network
@needs_docker
def test_scenario_bundle_hermetic_post_install(workspace: Path, tmp_path: Path) -> None:
    pyproject = workspace / "pyproject.toml"
    original = pyproject.read_text()
    pyproject.write_text(
        original + '\n[tool.funcd-bundle]\nhermetic = true\npost-install = ["python", "-c", '
        "\"import os, orjson; open(os.path.join(os.environ['FUNCD_BUNDLE_DIR'], 'marker'), 'w')"
        '.write(orjson.__version__)"]\n'
    )
    try:
        out = bundle(workspace, "reader", host_platform(), tmp_path / "reader", hermetic=True, check=True)
    finally:
        pyproject.write_text(original)

    assert (out / "marker").read_text() == _locked(workspace, "orjson"), (
        "post-install ran with the bundle on its path"
    )
    assert (out / "py_common" / "__init__.py").is_file()
    assert not any(out.glob("funcd_shim*"))


def test_post_install_needs_hermetic(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "x"\nversion = "0"\n\n[tool.funcd-bundle]\npost-install = ["true"]\n'
            ),
            "funcdctl.yaml": MANIFEST,
            "src/handler.py": "",
        },
    )
    with pytest.raises(BundleError, match="hermetic"):
        bundle(tmp_path, tmp_path.name, host_platform(), tmp_path / "out", hermetic=False, check=False)


def test_cli_post_install_needs_hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "x"\nversion = "0"\n\n[tool.funcd-bundle]\npost-install = ["true"]\n'
            ),
            "funcdctl.yaml": MANIFEST,
            "src/handler.py": "",
        },
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_:
        main(["--out", str(tmp_path / "out")])
    assert exit_.value.code == 2, "a post-install without hermetic is a usage error"
    assert not (tmp_path / "out").exists(), "nothing is bundled"


def test_default_handler_follows_funcdctl(tmp_path: Path) -> None:
    _write(
        tmp_path, {"counter.funcdctl.yaml": "runtime: python314\n", "counter.py": "def handle(c, e): ...\n"}
    )
    assert discover(tmp_path)["counter"].handler == tmp_path / "counter.py", (
        "a stem manifest's default is <name>.py"
    )
    _write(tmp_path / "g", {"funcdctl.yaml": "runtime: python314\n", "handler.py": ""})
    assert discover(tmp_path / "g")["g"].handler == tmp_path / "g" / "handler.py", (
        "the generic default is handler.py"
    )


def test_issue_154_stem_manifest_without_main_names_bundled_handler(tmp_path: Path) -> None:
    manifest = "runtime: python314\nhandler: handle\n"
    project = tmp_path / "project"
    _write(
        project,
        {
            "pyproject.toml": '[project]\nname = "extract"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
            "extract.funcdctl.yaml": manifest,
            "extract.py": "def handle(ctx, event): ...\n",
        },
    )
    _uv("lock", "--offline", "--python", "3.14", cwd=project)
    out = bundle(project, "extract", host_platform(), tmp_path / "extract", hermetic=False, check=False)

    assert discover(out)["extract"].handler == out / "extract.py", (
        "the bundle's generic manifest resolves to the bundled handler, not handler.py"
    )
    assert (out / "funcdctl.yaml").read_text() == manifest + "main: extract.py\n"


@pytest.mark.parametrize(
    "main",
    [
        '"main": src/handler.py\n',
        "'main': src/handler.py\n",
        "main:\n  src/handler.py\n",
    ],
    ids=["double-quoted-key", "single-quoted-key", "value-on-next-line"],
)
def test_issue_r29_main_key_is_rewritten_not_duplicated(tmp_path: Path, main: str) -> None:
    project = tmp_path / "reader"
    _write(
        project,
        {
            "pyproject.toml": '[project]\nname = "reader"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
            "funcdctl.yaml": "runtime: python314\n" + main + "handler: handle\n",
            "src/handler.py": "def handle(ctx, event): ...\n",
        },
    )
    _uv("lock", "--offline", "--python", "3.14", cwd=project)
    out = bundle(project, "reader", host_platform(), tmp_path / "out", hermetic=False, check=False)

    assert (out / "funcdctl.yaml").read_text() == "runtime: python314\nmain: handler.py\nhandler: handle\n", (
        "the manifest's one main names the bundled handler"
    )


@pytest.mark.parametrize(
    ("manifest", "want"),
    [
        ("{runtime: python314, handler: handle}\n", {"runtime": "python314", "handler": "handle"}),
        ("!!map &m {\n  runtime: python314,\n}\n", {"runtime": "python314"}),
        ("{}\n", {}),
    ],
    ids=["one-line", "tagged-multi-line", "empty"],
)
def test_issue_r49_flow_mapping_manifest_gets_main(
    tmp_path: Path, manifest: str, want: dict[str, str]
) -> None:
    project = tmp_path / "reader"
    _write(
        project,
        {
            "pyproject.toml": '[project]\nname = "reader"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
            "funcdctl.yaml": manifest,
            "handler.py": "def handle(ctx, event): ...\n",
        },
    )
    _uv("lock", "--offline", "--python", "3.14", cwd=project)
    out = bundle(project, "reader", host_platform(), tmp_path / "out", hermetic=False, check=False)

    assert yaml.safe_load((out / "funcdctl.yaml").read_text()) == want | {"main": "handler.py"}, (
        "the bundle's manifest is valid YAML whose main names the bundled handler"
    )


def test_missing_handler_is_named(tmp_path: Path) -> None:
    _write(tmp_path, {"funcdctl.yaml": "runtime: python314\nmain: src/nope.py\n"})
    with pytest.raises(BundleError, match="nope.py"):
        bundle(tmp_path, tmp_path.name, host_platform(), tmp_path / "out", hermetic=False, check=False)


def test_issue_r48_invalid_manifest_yaml_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, {"funcdctl.yaml": "runtime: [\n", "handler.py": ""})
    monkeypatch.chdir(tmp_path)

    assert main(["--no-check", "--out", str(tmp_path / "out")]) == 1, "a manifest that does not parse fails"
    err = capsys.readouterr().err
    assert err.startswith(f"funcd-bundle: {tmp_path / 'funcdctl.yaml'}: "), err
    assert "while parsing a flow node" in err, "the parser message names the problem"
    assert "Traceback" not in err
