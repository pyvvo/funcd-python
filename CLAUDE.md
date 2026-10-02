# funcd-python — agent working agreement

This repo holds the Python side of funcd: the Python runtime shim and the Python example
functions. The platform itself (Go daemon, API, CLI, e2e tests, providers, ADRs) lives in
[pyvvo/funcd](https://github.com/pyvvo/funcd).

## ⛔ Nothing about the dev machine ever enters the repo

No absolute OS paths, no local username, no personal email: not in files, commit messages,
examples or grep patterns. Paths are repo-root-relative. The only identity is `green-0-rabbit`,
`github.com/pyvvo` and "The funcd Authors".

## ⛔ No real financial data, no bank names

`examples/releve-lakehouse` parses bank statements. Real statements and anything derived from them
(PDF, Parquet, DuckDB files) never enter the repo, and the bank is only ever called "Bank".

## Decisions live in funcd

Design decisions are ADRs in funcd ([`docs/adr/`](https://github.com/pyvvo/funcd/tree/main/docs/adr)). This repo implements them and never
decides on its own. A change to the contract between funcd and the shim (the `FUNCD_*` env vars,
the health endpoints, the invoke socket, log capture, trace spans) needs a funcd ADR first.

## Layout

| Path | What |
|---|---|
| `shim/` | The `funcd_shim` package, stdlib-only. `embed.go` is the Go package funcd imports. It lists every module by name, and `embed_test.go` fails when a module is missing |
| `bundle/` | The `funcd-bundle` package (funcd ADR-0144): `uv run funcd-bundle` bundles a function and its locked dependencies for the runtime's Linux platform |
| `examples/*` | Example functions, each its own uv project with a path dependency on `../../shim` |
| `go.mod` | This repo is also a Go module. funcd pins it by git tag |

## Toolchain

`flake.nix` pins Python 3.14, uv, ruff, Go, just and lefthook, and turns off uv's own Python
downloads. Run everything through the dev shell:

```bash
nix develop -c just ci
```

The dev shell also installs the lefthook git hooks. pre-commit formats and lints staged files
with ruff and gofmt, commit-msg enforces Conventional Commits, and pre-push runs `just check`.
CI runs the same checks, so never bypass a hook with `--no-verify`.

## Rules

- **ruff owns formatting**, from the flake so every project uses one version. Run `just fmt`
  instead of formatting by hand. CI runs `ruff format --check`.
- **The contract lives in `funcdctl.yaml`.** No example derives or commits a schema file, and the
  manifest's `main` names the handler source. A handler without third-party dependencies is pushed as
  is; one with dependencies is bundled by `funcd-bundle`.
- **Bundles are not committed.** `uv run funcd-bundle` writes `dist/<name>/`, a per-architecture
  closure. funcd's `duckdb` lane builds the catalog-quack bundle itself.
- **Lockfiles are exact.** CI runs `uv run --locked`, so a `pyproject.toml` change needs `uv lock`.
- **Conventional Commits.** A PR title must be a Conventional Commit, and CI checks it. PRs are
  squash-merged through a merge queue, which uses the PR title as the commit message on `main`, so
  nobody can edit the message at merge time. The queue checks that exact message again before it
  lands. `main` takes no direct pushes, and the ruleset has no bypass, not even for admins.
- **release-please owns versions.** Never edit `version.txt` or `CHANGELOG.md` by hand, and never
  create tags. The funcd release GitHub App opens the release PR, which goes through the merge queue
  like any other PR. Merging it tags `vX.Y.Z`. The version in `shim/pyproject.toml` stays
  fixed on purpose: a bump would make every `uv.lock` stale, so the tag is the version.
  CI skips its `ci` job: the PR only bumps versions on an already checked `main`.
- **Every release publishes `funcd-shim` and `funcd-bundle` to PyPI** from the release workflow, with
  PyPI trusted publishing (no token). The workflow stamps the tag's version into the builds only. The
  public API is `funcd_shim`'s types, `funcd_shim.build` (the `build` extra) and the `funcd-bundle`
  command and its `[tool.funcd-bundle]` keys: removing or changing one is a `feat!:`.
- **Before 1.0, a breaking change (`feat!:`) bumps the minor version.** From v2.0.0 on, Go requires
  a `/v2` module path, so stay below v2.
- YAML is block style, imports sit at the top of the module, and comments explain why, not what.
