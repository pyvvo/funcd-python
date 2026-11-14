"""py-token-owner: the owner of the Secret py-demo-token. Each call writes the key TOKEN or deletes it,
then returns TOKEN as the next read returns it."""

# region handler
from typing import Literal, NotRequired, TypedDict

from funcd_shim import CloudEvent, FunctionContext


class FuncInput(TypedDict):
    """A put writes value into TOKEN; a delete removes TOKEN."""

    op: Literal["put", "delete"]
    value: NotRequired[str]


class FuncOutput(TypedDict):
    """TOKEN as read after the write, or None when py-demo-token no longer holds it."""

    token: str | None


def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    data = event["data"]
    if data["op"] == "put":
        context.secret.put("py-demo-token", "TOKEN", data.get("value", ""))
    elif data["op"] == "delete":
        context.secret.delete("py-demo-token", "TOKEN")
        # TOKEN is gone now: deleting a key that the Secret does not hold succeeds too.
        context.secret.delete("py-demo-token", "TOKEN")
    return {"token": context.secret.get("TOKEN")}


# endregion handler
