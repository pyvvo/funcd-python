# funcd-python

The Python runtime shim and the Python example functions for
[funcd](https://github.com/pyvvo/funcd), a single-binary serverless platform.

| Path | What |
|---|---|
| `shim/` | The shim that loads a function's handler inside a funcd worker. It has no runtime dependency |
| `examples/` | Example functions. Small build outputs are committed, dependency bundles are not |

funcd pins this repo as a Go module at a release tag, embeds the shim's modules, and runs the
examples in its e2e tests and lanes.

## Read config, secrets and lakes

A handler reads its bound values through the context (funcd ADR-0245):

```python
level = context.config.get("LOG_LEVEL")
key = context.secret.get("STRIPE_KEY")
lake = context.lake.get("lake")  # {"url": ..., "token": ...}
```

`config` holds the keys of the Function's bound ConfigMaps and `secret` those of its Secrets; a key a secret overrides
reads only through `secret`, and an unbound key reads as `None`. `list()` returns the bound keys, sorted.
`secret` asks funcd on every call (funcd ADR-0249), so no secret sits in the worker's environment.
`lake.get(alias)` raises `KeyError` naming the alias when it is not bound.

## Know who signed in

When funcd's edge signs the caller in with an OpenID Connect provider (funcd ADR-0335), `context.principal` says who
it is. It is `None` for a call that nobody signed in to:

```python
def handle(context: FunctionContext, event: CloudEvent[Any]) -> dict[str, str]:
    principal = context.principal
    return {"hello": principal.get("name", "stranger") if principal else "stranger"}
```

`"provider"` is the OIDCProvider's name, `"id"` the user's `sub` claim, `"name"` the first of the claims `name`,
`preferred_username` and `email` that is not empty, and `"claims"` the payload of the token that signed the user in.
`funcd_shim.Principal` types the dict.

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
