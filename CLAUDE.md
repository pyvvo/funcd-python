# funcd-python — agent working agreement

This repo holds the Python side of funcd: the Python runtime shim and the Python example
functions. The platform itself (Go daemon, API, CLI, e2e tests, providers, ADRs) lives in the
funcd repo.

## ⛔ Nothing about the dev machine ever enters the repo

No absolute OS paths, no local username, no personal email: not in files, commit messages,
examples or grep patterns. Paths are repo-root-relative. The only identity is `green-0-rabbit`,
`github.com/pyvvo` and "The funcd Authors".

## ⛔ No real financial data, no bank names

`examples/releve-lakehouse` parses bank statements. Real statements and anything derived from them
(PDF, Parquet, DuckDB files) never enter the repo, and the bank is only ever called "Bank".

## Decisions live in funcd

Design decisions are ADRs in the funcd repo (`docs/adr/`). This repo implements them and never
decides on its own. A change to the contract between funcd and the shim (the `FUNCD_*` env vars,
the health endpoints, the invoke socket, log capture, trace spans) needs a funcd ADR first.

## Layout

| Path | What |
|---|---|
| `shim/` | The `funcd_shim` package, stdlib-only. `embed.go` is the Go package funcd imports. It lists every module by name, and `embed_test.go` fails when a module is missing |
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
- **Small built files are committed.** kv-counter and log-burst commit their handler with baked
  validators and its contract schema. After changing one, run `just build` and commit the outputs.
  CI fails when a build changes a committed file.
- **Bundles are not committed.** A `bundle/` is a native, per-architecture dependency closure built
  in Docker. funcd's `duckdb` lane builds the catalog-quack bundle itself.
- **Lockfiles are exact.** CI runs `uv run --locked`, so a `pyproject.toml` change needs `uv lock`.
- **Conventional Commits.** A PR title must be a Conventional Commit, and CI checks it. PRs are
  squash-merged through a merge queue, which uses the PR title as the commit message on `main`, so
  nobody can edit the message at merge time. The queue checks that exact message again before it
  lands. `main` takes no direct pushes, and the ruleset has no bypass, not even for admins.
- **release-please owns versions.** Never edit `version.txt` or `CHANGELOG.md` by hand, and never
  create tags. The funcd release GitHub App opens the release PR, which goes through the merge queue
  like any other PR. Merging it tags `vX.Y.Z`. The version in `shim/pyproject.toml` stays
  fixed on purpose: a bump would make every `uv.lock` stale, so the tag is the version.
- **Before 1.0, a breaking change (`feat!:`) bumps the minor version.** From v2.0.0 on, Go requires
  a `/v2` module path, so stay below v2.
- YAML is block style, imports sit at the top of the module, and comments explain why, not what.
