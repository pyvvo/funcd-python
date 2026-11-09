"""Tests for the Python kv-claim function — the author's own unit tests, no platform needed.

A fake ``context.kv`` keeps one version per key, so these exercise the handler's claim, renew and release
logic: of two claims of one key the first wins and the second learns the holder; a renewal or a release names
the version.
"""

from typing import Any, cast

from funcd_shim import CloudEvent, FunctionContext
from funcd_shim.kv import KVClient, KVEntry

from main import FuncInput, handle


class _KV(KVClient):
    """An in-memory stand-in for the real KVClient: binding+key → (value, version)."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], KVEntry] = {}
        self.n = 0

    def _set(self, binding: str, key: str, value: bytes | str) -> str:
        self.n += 1
        body = value.encode() if isinstance(value, str) else value
        self.store[(binding, key)] = KVEntry(body, f"t-{self.n}")
        return f"t-{self.n}"

    def get_entry(self, binding: str, key: str) -> KVEntry | None:
        return self.store.get((binding, key))

    def put_if_absent(
        self, binding: str, key: str, value: bytes | str, *, ttl: int | None = None
    ) -> str | None:
        return None if (binding, key) in self.store else self._set(binding, key, value)

    def compare_and_set(
        self, binding: str, key: str, value: bytes | str, version: str, *, ttl: int | None = None
    ) -> str | None:
        held = self.store.get((binding, key))
        return self._set(binding, key, value) if held and held.version == version else None

    def delete_if(self, binding: str, key: str, version: str) -> bool:
        held = self.store.get((binding, key))
        if held is None or held.version != version:
            return False
        del self.store[(binding, key)]
        return True


class _Ctx:
    def __init__(self) -> None:
        self.kv: KVClient = _KV()

    def log(self, *args: object) -> None:
        pass

    def invoke(self, alias: str, payload: Any) -> Any:
        raise NotImplementedError


def _event(data: FuncInput) -> CloudEvent[FuncInput]:
    return {"id": "1", "source": "s", "type": "t", "data": data}


def test_the_first_claim_wins_and_the_second_learns_the_holder() -> None:
    ctx = cast(FunctionContext, _Ctx())
    won = handle(ctx, _event({"key": "run-1", "holder": "a", "ttl": 30}))
    assert won == {"key": "run-1", "won": True, "version": "t-1", "holder": "a"}
    assert handle(ctx, _event({"key": "run-1", "holder": "b"})) == {
        "key": "run-1",
        "won": False,
        "version": "t-1",
        "holder": "a",
    }


def test_renew_and_release_name_the_version() -> None:
    ctx = cast(FunctionContext, _Ctx())
    handle(ctx, _event({"key": "lease", "holder": "a"}))
    stale = handle(ctx, _event({"key": "lease", "holder": "a", "op": "renew", "version": "t-0"}))
    assert stale["won"] is False
    renewed = handle(ctx, _event({"key": "lease", "holder": "a", "op": "renew", "version": "t-1"}))
    assert renewed == {"key": "lease", "won": True, "version": "t-2", "holder": "a"}
    assert (
        handle(ctx, _event({"key": "lease", "holder": "a", "op": "release", "version": "t-1"}))["won"]
        is False
    )
    released = handle(ctx, _event({"key": "lease", "holder": "a", "op": "release", "version": "t-2"}))
    assert released == {"key": "lease", "won": True, "version": "", "holder": ""}
