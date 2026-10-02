"""Tests for the fn-to-fn ``context.invoke`` client (ADR-0064)."""

from __future__ import annotations

import pytest

from funcd_shim.invoke import invoke
from funcd_shim.types import FunctionContext


@pytest.mark.parametrize(
    "doc", [invoke.__doc__, FunctionContext.invoke.__doc__], ids=["invoke", "FunctionContext.invoke"]
)
def test_issue_r18_invoke_docs_state_data_key_unwrap(doc: str | None) -> None:
    # The broker reads an input with a top-level data/specversion key as a full envelope and hands the
    # callee only its data (funcd ADR-0134), so the docs must state the rule and the explicit-envelope escape.
    assert doc is not None
    assert '"data"' in doc
    assert '"specversion"' in doc
    assert '{"specversion": "1.0", "data": payload}' in doc
