# Run every recipe through the pinned dev shell: `nix develop -c just <recipe>`.

python := "3.14"
projects := "shim examples/catalog-quack examples/hello-world examples/kv-counter examples/log-burst"
built := "examples/kv-counter examples/log-burst"
# releve-lakehouse drafts its deps without a uv.lock, so it gets ruff only
linted := projects + " examples/releve-lakehouse"

default:
    @just --list

# format and apply ruff's safe lint fixes across the shim and every example
fmt:
    #!/usr/bin/env bash
    set -euo pipefail
    for d in {{linted}}; do
        (cd "$d" && ruff format . && ruff check --fix .)
    done

# ruff (the flake's single version), mypy and pytest for the shim and every example that has tests;
# --locked fails on a stale uv.lock
check:
    #!/usr/bin/env bash
    set -euo pipefail
    for d in {{linted}}; do
        (cd "$d" && ruff format --check . && ruff check .)
    done
    for d in {{projects}}; do
        echo "== $d"
        (cd "$d" && uv run --locked --python {{python}} mypy && uv run --locked --python {{python}} pytest -q)
    done

# rebuild the committed example outputs: the handler with baked validators and its contract schema
build:
    #!/usr/bin/env bash
    set -euo pipefail
    for d in {{built}}; do
        (cd "$d" && uv run --locked --python {{python}} --group build python build.py)
    done

# the Go embed package funcd imports
go-check:
    go vet ./...
    go build ./...
    go test ./...

# the CI gate: fails when a build changed a committed file
ci: check build go-check
    @if [ -n "$(git status --porcelain)" ]; then git status --short; echo "build outputs are stale: run 'just build' and commit the result"; exit 1; fi
