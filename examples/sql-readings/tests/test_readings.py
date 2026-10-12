from typing import Any, cast

from fakesql import FakeContext
from funcd_shim import CloudEvent, FunctionContext

import ingest
import summarize


def _event(data: Any) -> CloudEvent[Any]:
    return cast(CloudEvent[Any], {"id": "1", "source": "/test", "type": "test", "data": data})


def test_the_steps_store_then_summarize_a_sensor() -> None:
    ctx = cast(FunctionContext, FakeContext())
    first = ingest.handle(ctx, _event({"sensor": "greenhouse", "values": [21.5, 22.0, 23.5]}))
    assert first == {"sensor": "greenhouse", "stored": 3}
    ingest.handle(ctx, _event({"sensor": "cellar", "values": [12.0]}))
    out = summarize.handle(
        ctx, _event(ingest.handle(ctx, _event({"sensor": "greenhouse", "values": [22.5]})))
    )

    assert (out["count"], out["low"], out["high"]) == (4, 21.5, 23.5)
    assert out["mean"] == 22.375
    assert [(p["seq"], p["value"], p["moving_avg"], p["rank"]) for p in out["latest"]] == [
        (2, 22.0, 21.75, 3),
        (3, 23.5, 22.333333333333332, 1),
        (4, 22.5, 22.666666666666668, 2),
    ], "each value with the average of it and the two before, and its rank by value"
