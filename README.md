# funcd-python

The Python runtime shim and the Python example functions for
[funcd](https://github.com/pyvvo/funcd), a single-binary serverless platform.

| Path | What |
|---|---|
| `shim/` | The stdlib-only shim that loads a function's handler inside a funcd worker |
| `examples/` | Example functions. Small build outputs are committed, dependency bundles are not |

funcd pins this repo as a Go module at a release tag, embeds the shim's modules, and runs the
examples in its e2e tests and lanes.

## Develop

```bash
nix develop -c just ci
```

The dev shell also installs the git hooks. They format and lint staged files, check the commit
message, and run the tests before a push.

## Releases

Versions follow semver and come from [release-please](https://github.com/googleapis/release-please).
PR titles are Conventional Commits, and merging the release PR tags `vX.Y.Z`.

Some example READMEs mention funcd's `just` recipes and `e2e/` suites. Those live in
[pyvvo/funcd](https://github.com/pyvvo/funcd).

## License

[Apache-2.0](LICENSE). Copyright 2026 The funcd Authors.
