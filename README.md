# funcd-python

The Python runtime shim and the Python example functions for
[funcd](https://github.com/pyvvo/funcd), a single-binary serverless platform.

| Path | What |
|---|---|
| `shim/` | The shim that loads a function's handler inside a funcd worker. Its one runtime dependency is fastjsonschema |
| `examples/` | Example functions. Small build outputs are committed, dependency bundles are not |

funcd pins this repo as a Go module at a release tag, embeds the shim's modules, and runs the
examples in its e2e tests and lanes.

## Read config, secrets and catalogs

A handler reads its bound values through the context (funcd ADR-0245):

```python
level = context.config.get("LOG_LEVEL")
key = context.secret.get("STRIPE_KEY")
lake = context.catalog.get("lake")  # {"url": ..., "token": ...}
```

`config` holds the keys of the Function's bound ConfigMaps and `secret` those of its Secrets; a key a secret overrides
reads only through `secret`, and an unbound key reads as `None`. `list()` returns the bound keys, sorted.
`secret` asks funcd on every call (funcd ADR-0249), so no secret sits in the worker's environment.
`catalog.get(alias)` raises `KeyError` naming the alias when it is not bound.

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
