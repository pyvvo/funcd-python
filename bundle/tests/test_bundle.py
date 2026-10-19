"""Scenario tests for funcd-bundle (funcd ADR-0144, ADR-0247).

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

from funcd_bundle import BundleError, bundle, discover, host_platform
from funcd_bundle.bundle import BASE_IMAGES, PLATFORMS, _check
from funcd_bundle.cli import main

FOREIGN = {"linux/arm64": "linux/amd64", "linux/amd64": "linux/arm64"}
ELF_MACHINE = {"linux/amd64": 0x3E, "linux/arm64": 0xB7}
CONTRACT = "runtime: python314\ninput:\n  type: object\noutput:\n  type: object\n"
CONTRACT_JSON = '{"runtime": "python314", "input": {"type": "object"}, "output": {"type": "object"}}\n'
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


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
            "functions/reader/reader.contract.yaml": CONTRACT,
            "functions/reader/src/main.py": (
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

    assert (out / "main.py").is_file()
    assert (out / "reader.contract.yaml").read_text() == CONTRACT, (
        "the contract file sits at the bundle's root"
    )
    assert not (out / "funcdctl.yaml").exists(), "no manifest is written"
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
            "old.contract.yaml": CONTRACT,
            "src/main.py": "import yaml\n",
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
                '\n[tool.funcd-bundle.functions]\nx = "src/main.py"\n'
            ),
            "x.contract.yaml": CONTRACT,
            "src/main.py": "",
        },
    )
    with pytest.raises(BundleError, match="hermetic"):
        bundle(tmp_path, "x", host_platform(), tmp_path / "out", hermetic=False, check=False)


def test_cli_post_install_needs_hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "x"\nversion = "0"\n\n[tool.funcd-bundle]\npost-install = ["true"]\n'
                '\n[tool.funcd-bundle.functions]\nx = "src/main.py"\n'
            ),
            "x.contract.yaml": CONTRACT,
            "src/main.py": "",
        },
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_:
        main(["--out", str(tmp_path / "out")])
    assert exit_.value.code == 2, "a post-install without hermetic is a usage error"
    assert not (tmp_path / "out").exists(), "nothing is bundled"


# scenario: dev-python-in-place (the entries funcd-bundle and funcdctl dev share)
def test_functions_table_names_each_entry(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "app"\nversion = "0"\n\n[tool.funcd-bundle.functions]\n'
                'intake = "src/intake/main.py"\nstats = "src/stats/main.py"\n'
            ),
            "intake.contract.yaml": CONTRACT,
            "stats.contract.json": CONTRACT_JSON,
            "src/intake/main.py": "",
            "src/stats/main.py": "",
        },
    )
    found = discover(tmp_path)
    assert {n: f.handler for n, f in found.items()} == {
        "intake": tmp_path / "src/intake/main.py",
        "stats": tmp_path / "src/stats/main.py",
    }
    assert found["stats"].contract == tmp_path / "stats.contract.json"


def test_without_the_table_one_function_named_after_the_folder(tmp_path: Path) -> None:
    project = tmp_path / "counter"
    _write(project, {"counter.contract.yaml": CONTRACT, "src/main.py": ""})
    [(name, fn)] = discover(project).items()
    assert (name, fn.handler) == ("counter", project / "src/main.py")


def test_every_example_names_an_entry_and_a_contract_it_ships() -> None:
    examples = sorted(p.parent for p in EXAMPLES.glob("*/pyproject.toml"))
    assert examples, f"no example projects under {EXAMPLES}"
    missing = []
    for example in examples:
        try:
            discover(example)
        except BundleError as err:
            missing.append(str(err))
    assert not missing, f"funcdctl dev and funcd-bundle cannot find these functions: {missing}"


# scenario: bundlers-copy-the-contract
def test_scenario_bundlers_copy_the_contract(tmp_path: Path) -> None:
    project = tmp_path / "reader"
    _write(
        project,
        {
            "pyproject.toml": '[project]\nname = "reader"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
            "reader.contract.json": CONTRACT_JSON,
            "src/main.py": "def handle(ctx, event): ...\n",
        },
    )
    _uv("lock", "--offline", "--python", "3.14", cwd=project)
    out = bundle(project, "reader", host_platform(), tmp_path / "out", hermetic=False, check=False)

    assert (out / "reader.contract.json").read_text() == CONTRACT_JSON
    assert (out / "main.py").is_file(), "the entry sits at the bundle's root, where push looks for main.py"
    assert not (out / "funcdctl.yaml").exists()


def test_scenario_bundlers_copy_the_contract_a_function_without_one_fails(tmp_path: Path) -> None:
    _write(tmp_path / "reader", {"src/main.py": ""})
    with pytest.raises(BundleError, match=r"no contract file: neither .*reader\.contract\.yaml nor .*\.json"):
        discover(tmp_path / "reader")


def test_two_contract_files_of_one_function_fail(tmp_path: Path) -> None:
    _write(
        tmp_path / "reader",
        {"reader.contract.yaml": CONTRACT, "reader.contract.json": CONTRACT_JSON, "src/main.py": ""},
    )
    with pytest.raises(BundleError, match="two contract files"):
        discover(tmp_path / "reader")


# scenario: old-manifest-refused
@pytest.mark.parametrize("manifest", ["funcdctl.yaml", "reader.funcdctl.yaml"])
def test_scenario_old_manifest_refused(
    tmp_path: Path, manifest: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "reader"
    _write(project, {"reader.contract.yaml": CONTRACT, "src/main.py": "", manifest: "runtime: python314\n"})
    monkeypatch.chdir(project)

    assert main(["--no-check", "--out", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert f"{project / manifest}: funcdctl.yaml is no longer read" in err, err
    assert "<name>.contract.yaml" in err and "[tool.funcd-bundle.functions]" in err, (
        "it names what replaces it"
    )
    assert not (tmp_path / "out").exists(), "nothing is bundled"


def test_missing_entry_is_named(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "x"\n\n[tool.funcd-bundle.functions]\nx = "src/nope.py"\n',
            "x.contract.yaml": CONTRACT,
        },
    )
    with pytest.raises(BundleError, match="nope.py"):
        discover(tmp_path)


def test_invalid_functions_table_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, {"pyproject.toml": '[project]\nname = "x"\n\n[tool.funcd-bundle.functions]\nx = 1\n'})
    monkeypatch.chdir(tmp_path)

    assert main(["--no-check", "--out", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert "[tool.funcd-bundle.functions] must map each function name to its entry" in err, err
    assert "Traceback" not in err


def test_base_image_is_pinned_by_digest() -> None:
    assert set(BASE_IMAGES) == set(PLATFORMS), "one pinned image per platform a bundle builds for"
    for platform, ref in BASE_IMAGES.items():
        assert re.fullmatch(r"python@sha256:[0-9a-f]{64}", ref), f"{platform}: {ref}"
