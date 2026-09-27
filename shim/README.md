# funcd-shim

The Python side of [funcd](https://github.com/pyvvo/funcd): the runtime shim that loads a
function's handler inside a funcd worker, the types handlers are written against, and the
contract build.

```bash
pip install "funcd-shim[build]"
```

- `from funcd_shim import CloudEvent, FunctionContext` types a handler.
- `from funcd_shim.build import build` bakes its input and output contract. It needs the `build`
  extra.
