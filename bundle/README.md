# funcd-bundle

Bundles a Python [funcd](https://github.com/pyvvo/funcd) function for deployment: the handler, the exact
versions of its runtime dependencies from `uv.lock` installed for the funcd runtime's Linux platform, and
its contract file (funcd ADR-0247). `funcdctl push` turns the bundle into one OCI artifact.

```bash
uv add --dev funcd-bundle
uv run funcd-bundle                          # dist/<name>/ for linux/<host arch>
uv run funcd-bundle --platform linux/amd64   # from any host
funcdctl push dist/<name> <ref>                # entry main.py, contract dist/<name>/<name>.contract.yaml
```

- **Functions**: each entry of `[tool.funcd-bundle.functions]` is a function, its name mapped to its entry,
  relative to the project root; without the table, the project is one function named after its directory,
  from `src/main.py`. `funcdctl dev` reads the same entries. An entry in a subdirectory is copied with its
  directory, so `src/<name>/main.py` lands as `main.py`, the entry `funcdctl push` assumes.
- **Contract**: each function's `<name>.contract.yaml` (or `.json`) at the project root is copied to its
  bundle's root, where `funcdctl push` reads its runtime and contract. A function with neither, or both,
  fails. A `funcdctl.yaml` or `<name>.funcdctl.yaml` fails the build: nothing reads it any more.

```toml
[tool.funcd-bundle.functions]
orders = "src/orders/main.py"
orders-migrate = "src/orders-migrate/main.py"
```

- **Dependencies**: `uv export --frozen --no-dev` of the project, without `funcd-shim` and the packages
  only it needs (the runtime ships them). Registry packages install as wheels only (`--only-binary
  :all:`) for CPython 3.14 and manylinux glibc 2.36, with their locked hashes. Workspace members are built
  as pure-Python wheels.
- **Check**: the vendored modules the handler imports are imported in `python:3.14-slim-bookworm` for the
  target platform (`--no-check` skips it). A foreign platform needs QEMU on the Docker host.
- **Hermetic**: `--hermetic`, or `hermetic = true` in `[tool.funcd-bundle]`, installs with pip inside
  that image and then runs `post-install`, with `FUNCD_BUNDLE_DIR` and `PYTHONPATH` set to the bundle:

```toml
[tool.funcd-bundle]
hermetic = true
post-install = ["python", "scripts/install_extensions.py"]
```

Docker steps copy files in and out with `docker cp` and bind no host path.
