"""py-token-user: a Function that binds the Secret py-demo-token but does not own it. Each call reads the
key TOKEN, then tries to write the call's value into it, which funcd refuses until a role allows it."""

# region handler
from typing import NotRequired, TypedDict

from funcd_shim import CloudEvent, FunctionContext


class FuncInput(TypedDict):
    value: str


class FuncOutput(TypedDict):
    """TOKEN as this call last read it, and the error of a refused write."""

    token: str | None
    error: NotRequired[str]


def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    token = context.secret.get("TOKEN")
    try:
        context.secret.put("py-demo-token", "TOKEN", event["data"]["value"])
    except RuntimeError as err:
        return {"token": token, "error": str(err)}
    return {"token": context.secret.get("TOKEN")}


# endregion handler
