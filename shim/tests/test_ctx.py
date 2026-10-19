"""Scenario tests for context.config, context.secret and context.catalog (funcd ADR-0245) over the environment
funcd gives a worker: ConfigMap app-settings (LOG_LEVEL, MODE), Secret api-keys (STRIPE_KEY, MODE) and catalog
lake."""

from __future__ import annotations

import pytest

from funcd_shim.catalog import CatalogClient
from funcd_shim.config import ConfigClient
from funcd_shim.secret import SecretClient

ENV = {
    "PATH": "/usr/bin",
    "FUNCD_INVOKE_SOCKET": "/run/funcd/api.sock",
    "LOG_LEVEL": "debug",
    "MODE": "prod",
    "STRIPE_KEY": "sk",
    "FUNCD_CONFIG_KEYS": "LOG_LEVEL",
    "FUNCD_SECRET_KEYS": "MODE,STRIPE_KEY",
    "FUNCD_CATALOG_LAKE_URL": "127.0.0.1:9100",
    "FUNCD_CATALOG_LAKE_TOKEN": "tok-a",
}


def test_ctx_config_get() -> None:
    # scenario: ctx-config-get
    config = ConfigClient(ENV)
    assert config.get("LOG_LEVEL") == "debug"
    assert config.list() == ["LOG_LEVEL"]


def test_ctx_secret_get() -> None:
    # scenario: ctx-secret-get
    secret = SecretClient(ENV)
    assert secret.get("STRIPE_KEY") == "sk"
    assert secret.list() == ["MODE", "STRIPE_KEY"]
    assert ConfigClient(ENV).get("STRIPE_KEY") is None


def test_ctx_secret_shadows_config() -> None:
    # scenario: ctx-secret-shadows-config
    assert SecretClient(ENV).get("MODE") == "prod"
    assert ConfigClient(ENV).get("MODE") is None


def test_ctx_unbound_key() -> None:
    # scenario: ctx-unbound-key
    assert ConfigClient(ENV).get("PATH") is None
    assert SecretClient(ENV).get("FUNCD_INVOKE_SOCKET") is None


def test_ctx_catalog_get() -> None:
    # scenario: ctx-catalog-get
    catalog = CatalogClient(ENV)
    assert catalog.get("lake") == {"url": "127.0.0.1:9100", "token": "tok-a"}
    with pytest.raises(KeyError, match='"other"'):
        catalog.get("other")


@pytest.mark.parametrize("lists", [{}, {"FUNCD_CONFIG_KEYS": "", "FUNCD_SECRET_KEYS": ""}])
def test_an_absent_or_empty_key_list_is_no_keys(lists: dict[str, str]) -> None:
    env = {**lists, "LOG_LEVEL": "debug", "STRIPE_KEY": "sk"}
    assert ConfigClient(env).list() == []
    assert ConfigClient(env).get("LOG_LEVEL") is None
    assert SecretClient(env).list() == []
    assert SecretClient(env).get("STRIPE_KEY") is None


def test_the_clients_read_os_environ_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    config = ConfigClient()
    monkeypatch.setenv("FUNCD_CONFIG_KEYS", "A")
    monkeypatch.setenv("A", "1")
    assert config.get("A") == "1"
    monkeypatch.setenv("A", "2")
    assert config.get("A") == "2"
