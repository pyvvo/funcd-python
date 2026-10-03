# Run every recipe through the pinned dev shell: `nix develop -c just <recipe>`.

python := "3.14"
# releve-lakehouse's handlers do not pass mypy --strict yet, so mypy skips it
typed := "shim bundle examples/catalog-quack examples/hello-world examples/kv-counter examples/log-burst"
projects := typed + " examples/releve-lakehouse"

default:
    @just --list

# format and apply ruff's safe lint fixes across the shim and every example
fmt:
    #!/usr/bin/env bash
    set -euo pipefail
    for d in {{projects}}; do
        (cd "$d" && ruff format . && ruff check --fix .)
    done

# ruff (the flake's single version), mypy and pytest for the shim and every example that has tests;
# --locked fails on a stale uv.lock
check:
    #!/usr/bin/env bash
    set -euo pipefail
    for d in {{projects}}; do
        (cd "$d" && ruff format --check . && ruff check .)
    done
    for d in {{typed}}; do
        echo "== mypy $d"
        (cd "$d" && uv run --locked --python {{python}} mypy)
    done
    for d in {{projects}}; do
        echo "== pytest $d"
        (cd "$d" && uv run --locked --python {{python}} pytest -q)
    done

# the Go embed package funcd imports
go-check:
    go vet ./...
    go build ./...
    go test ./...

# the CI gate: fails when a step changed a committed file
ci: check go-check
    @if [ -n "$(git status --porcelain)" ]; then git status --short; echo "a check changed committed files: commit the result"; exit 1; fi
