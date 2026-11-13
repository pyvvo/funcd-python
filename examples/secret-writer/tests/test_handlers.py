"""The author's unit tests: each handler runs against a fake ``context.secret`` over one Secret,
py-demo-token. Like funcd, the fake lets only a writer change the Secret, refuses another function's put with
the HTTP status 403, and takes the delete of a key that the Secret does not hold as no error."""

from typing import Any, cast

from funcd_shim import CloudEvent, FunctionContext
from funcd_shim.secret import SecretClient
from handlers import load

owner = load("py-token-owner")
user = load("py-token-user")

REFUSED = (
    'context.secret.put failed: 403 {"type":"urn:funcd:problem:forbidden","title":"Forbidden","status":403,'
    '"detail":"cedar: forbidden by policy builtin/secret-single-writer"}'
)


class _Secrets(SecretClient):
    """An in-memory py-demo-token, key to value, with no socket."""

    def __init__(self, writer: bool) -> None:
        super().__init__()
        self.writer = writer
        self.data = {"TOKEN": "token-v1"}
        self.calls: list[str] = []

    def get(self, key: str) -> str | None:
        self.calls.append(f"get {key}")
        return self.data.get(key)

    def put(self, binding: str, key: str, value: str) -> None:
        self.calls.append(f"put {binding} {key}")
        if not self.writer:
            raise RuntimeError(REFUSED)
        self.data[key] = value

    def delete(self, binding: str, key: str) -> None:
        self.calls.append(f"delete {binding} {key}")
        self.data.pop(key, None)


class _Ctx:
    def __init__(self, writer: bool) -> None:
        self.secret = _Secrets(writer)

    def log(self, *args: object) -> None:
        pass


def _event(data: Any) -> CloudEvent[Any]:
    return cast(CloudEvent[Any], {"id": "1", "source": "/test", "type": "test", "data": data})


def test_the_owner_writes_token_and_returns_the_value_that_it_reads_next() -> None:
    ctx = _Ctx(writer=True)
    out = owner.handle(cast(FunctionContext, ctx), _event({"op": "put", "value": "token-v2"}))
    assert out == {"token": "token-v2"}
    assert ctx.secret.data == {"TOKEN": "token-v2"}
    assert ctx.secret.calls == ["put py-demo-token TOKEN", "get TOKEN"]


def test_the_owner_deletes_token_twice_the_second_time_a_key_that_the_secret_no_longer_holds() -> None:
    ctx = _Ctx(writer=True)
    assert owner.handle(cast(FunctionContext, ctx), _event({"op": "delete"})) == {"token": None}
    assert ctx.secret.data == {}
    assert ctx.secret.calls == ["delete py-demo-token TOKEN", "delete py-demo-token TOKEN", "get TOKEN"]


def test_the_user_reads_token_and_reports_the_refusal_of_its_write() -> None:
    ctx = _Ctx(writer=False)
    out = user.handle(cast(FunctionContext, ctx), _event({"value": "token-v2"}))
    assert out == {"token": "token-v1", "error": REFUSED}
    assert ctx.secret.data == {"TOKEN": "token-v1"}


def test_the_user_writes_token_once_a_role_allows_it_and_returns_the_value_that_it_reads_next() -> None:
    ctx = _Ctx(writer=True)
    assert user.handle(cast(FunctionContext, ctx), _event({"value": "token-v2"})) == {"token": "token-v2"}
    assert ctx.secret.data == {"TOKEN": "token-v2"}
