# secret-writer (Python) — Functions that write a key of a Secret

The Python sibling of
[`examples/secret-writer` in pyvvo/funcd-typescript](https://github.com/pyvvo/funcd-typescript/tree/main/examples/secret-writer).
Two functions bind the Secret `py-demo-token`, whose key `TOKEN` starts with the dummy value `token-v1`:

| Function | What |
|---|---|
| `py-token-owner` | the Secret's owner, which `spec.owner` names: a put writes the call's value into `TOKEN` with `context.secret.put`, and a delete removes `TOKEN` with `context.secret.delete`, twice, since deleting a key that the Secret no longer holds is no error. Each call returns `TOKEN` as the next read returns it |
| `py-token-user` | binds the Secret but does not own it: each call reads `TOKEN` and tries to write the call's value. funcd refuses the write with the HTTP status 403 until [`fixtures/grant.yaml`](fixtures/grant.yaml) gives the function the role `Secret Data Writer` on `py-demo-token` |

## Run it locally

`funcdctl dev` runs both functions from their entries, which `pyproject.toml` lists under
`[tool.funcd-bundle.functions]`, with the `python3` on the `PATH`, version 3.12 or later. Neither function needs a
third-party package. From the repository's root folder:

```bash
funcdctl dev examples/secret-writer --gport 8080 --cport 8081
```

In another terminal:

```bash
curl -s -X POST -H 'content-type: application/json' -d '{"op":"put","value":"token-v2"}' http://127.0.0.1:8080/function/py-token-owner
# → {"token":"token-v2"}
curl -s -X POST -H 'content-type: application/json' -d '{"value":"token-v3"}' http://127.0.0.1:8080/function/py-token-user
# → {"token":"token-v2","error":"context.secret.put failed: 403 …"}
funcdctl --server http://127.0.0.1:8081 --token funcd-dev-token apply -f examples/secret-writer/fixtures/grant.yaml
curl -s -X POST -H 'content-type: application/json' -d '{"value":"token-v3"}' http://127.0.0.1:8080/function/py-token-user
# → {"token":"token-v3"}
curl -s -X POST -H 'content-type: application/json' -d '{"op":"delete"}' http://127.0.0.1:8080/function/py-token-owner
# → {"token":null}
```

## Files

- `secret.yaml`: the Secret `py-demo-token`, owned by `py-token-owner`.
- `py-token-owner.yaml`, `py-token-user.yaml`: the functions, each declaring and binding `py-demo-token`.
- `src/py-token-owner/main.py`, `src/py-token-user/main.py`: the handlers, each pushed as one file with the contract
  file of its name.
- `fixtures/grant.yaml`: the RolesAssignment that lets `py-token-user` write `py-demo-token`. `funcdctl dev` reads only
  the folder's top-level files, so it does not apply this one.

## Tests

`uv run pytest` runs both handlers against a fake `context.secret` that refuses a put from a function that may not
write, and checks each entry with strict mypy. funcd's `secret-writer` lane runs the example on containerd.
