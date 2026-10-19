"""Scenario tests for context.secret on funcd's local API (funcd ADR-0249): ``GET /secret`` and
``GET /secret/{key}`` over a fake funcd that binds Secret api-keys (STRIPE_KEY sk, MODE prod) and ConfigMap
app-settings (MODE dev, so funcd lists no MODE in FUNCD_CONFIG_KEYS)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fakeapi import DependencyAPI

from funcd_shim.config import ConfigClient
from funcd_shim.secret import SecretClient


def _value(v: str) -> tuple[int, bytes]:
    return 200, json.dumps({"value": v}).encode()


@pytest.fixture
def api(dependency_api: DependencyAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[DependencyAPI]:
    monkeypatch.setenv("FUNCD_INVOKE_SOCKET", dependency_api.path)
    dependency_api.routes["/secret"] = (200, json.dumps({"keys": ["MODE", "STRIPE_KEY"]}).encode())
    dependency_api.routes["/secret/STRIPE_KEY"] = _value("sk")
    dependency_api.routes["/secret/MODE"] = _value("prod")
    dependency_api.replies["-"] = (404, b'{"title":"Not Found"}')
    yield dependency_api


def test_secret_read_from_funcd(api: DependencyAPI) -> None:
    # scenario: secret-read-from-funcd — get returns a bound value and list the sorted keys, read on every
    # call.
    secret = SecretClient()
    assert secret.get("STRIPE_KEY") == "sk"
    assert secret.list() == ["MODE", "STRIPE_KEY"]
    api.routes["/secret/STRIPE_KEY"] = _value("sk2")
    assert secret.get("STRIPE_KEY") == "sk2", "no cache: the next read sees the change"
    assert api.calls == ["GET /secret/STRIPE_KEY -", "GET /secret -", "GET /secret/STRIPE_KEY -"]


def test_secret_unbound(api: DependencyAPI) -> None:
    # scenario: secret-unbound — a key no bound Secret holds reads as None.
    assert SecretClient().get("NOPE") is None


def test_secret_shadows_config(api: DependencyAPI) -> None:
    # scenario: secret-shadows-config — a key a bound Secret holds reads only through secret.
    assert SecretClient().get("MODE") == "prod"
    assert ConfigClient({"FUNCD_CONFIG_KEYS": "LOG_LEVEL", "LOG_LEVEL": "debug"}).get("MODE") is None


def test_a_failed_check_raises(api: DependencyAPI) -> None:
    api.routes["/secret/STRIPE_KEY"] = (403, b'{"title":"Forbidden"}')
    api.routes["/secret"] = (403, b'{"title":"Forbidden"}')
    with pytest.raises(RuntimeError, match=r"context\.secret\.get failed: 403"):
        SecretClient().get("STRIPE_KEY")
    with pytest.raises(RuntimeError, match=r"context\.secret\.list failed: 403"):
        SecretClient().list()


def test_a_pool_member_names_itself_and_a_key_is_path_encoded(api: DependencyAPI) -> None:
    api.routes["/secret/a%2Fb"] = _value("v")
    assert SecretClient("member-a").get("a/b") == "v"
    assert api.calls == ["GET /secret/a%2Fb member-a"]
