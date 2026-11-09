"""kv-claim authored in Python: the sibling of funcd-typescript's ``examples/kv-claim``.

Each call races for a claim on a key in the KV binding ``claims`` with ``context.kv``'s conditional
writes: ``claim`` creates the key with the caller's holder (``put_if_absent``, with an optional ``ttl`` in
seconds); ``renew`` replaces it while it is at ``version`` (``compare_and_set`` with a new ``ttl``);
``release`` deletes it while it is at ``version`` (``delete_if``). It answers whether this call won and,
when it lost, the key's version and holder as they are now.
"""

from typing import NotRequired, TypedDict

from funcd_shim import CloudEvent, FunctionContext


class FuncInput(TypedDict):
    """The claim to race: the key, this caller's holder, and the operation."""

    key: str
    holder: str
    op: NotRequired[str]
    version: NotRequired[str]
    ttl: NotRequired[int]


class FuncOutput(TypedDict):
    """Whether this call won, and the key's version and holder."""

    key: str
    won: bool
    version: str
    holder: str


def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    """Claim, renew or release the key in the ``claims`` binding and say whether this call won."""
    data = event.get("data")
    if data is None:
        raise ValueError("kv-claim: the event carries no data")
    key, holder, op = data["key"], data["holder"], data.get("op", "claim")
    version, ttl = data.get("version", ""), data.get("ttl")
    kv = context.kv
    if op == "release":
        if kv.delete_if("claims", key, version):
            return {"key": key, "won": True, "version": "", "holder": ""}
    else:
        new = (
            kv.compare_and_set("claims", key, holder, version, ttl=ttl)
            if op == "renew"
            else kv.put_if_absent("claims", key, holder, ttl=ttl)
        )
        if new is not None:
            return {"key": key, "won": True, "version": new, "holder": holder}
    now = kv.get_entry("claims", key)
    if now is None:
        return {"key": key, "won": False, "version": "", "holder": ""}
    return {"key": key, "won": False, "version": now.version, "holder": now.value.decode("utf-8")}
