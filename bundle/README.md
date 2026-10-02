# funcd-bundle

Bundles a Python [funcd](https://github.com/pyvvo/funcd) function for deployment: the handler, the exact
versions of its runtime dependencies from `uv.lock` installed for the funcd runtime's Linux platform, and
its `funcdctl.yaml`. `funcdctl push` turns the bundle into one OCI artifact.

```bash
uv add --dev funcd-bundle
uv run funcd-bundle                          # dist/<name>/ for linux/<host arch>
uv run funcd-bundle --platform linux/amd64   # from any host
funcdctl push dist/<name> <ref> --entry handler.py
```

- **Functions**: every `<name>.funcdctl.yaml` in the project is a function `<name>`; with none,
  `funcdctl.yaml` is one named after the project directory. The handler is the manifest's `main`, else
  `<name>.py` (or `handler.py` for `funcdctl.yaml`) beside the manifest, as `funcdctl dev` reads it. A handler
  in a subdirectory is copied with its directory.
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
