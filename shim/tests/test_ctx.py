"""Scenario tests for context.config and context.lake (funcd ADR-0245) over the environment funcd gives a
worker: ConfigMap app-settings (LOG_LEVEL, MODE, which a bound Secret also holds, so it is not listed) and
lake lake. context.secret reads funcd's local API instead (test_secret.py, funcd ADR-0249)."""

from __future__ import annotations

import pytest

from funcd_shim.config import ConfigClient
from funcd_shim.lake import LakeClient

ENV = {
    "PATH": "/usr/bin",
    "FUNCD_INVOKE_SOCKET": "/run/funcd/api.sock",
    "LOG_LEVEL": "debug",
    "FUNCD_CONFIG_KEYS": "LOG_LEVEL",
    "FUNCD_LAKE_LAKE_URL": "127.0.0.1:9100",
    "FUNCD_LAKE_LAKE_TOKEN": "tok-a",
}


def test_ctx_config_get() -> None:
    # scenario: ctx-config-get
    config = ConfigClient(ENV)
    assert config.get("LOG_LEVEL") == "debug"
    assert config.list() == ["LOG_LEVEL"]


def test_ctx_unbound_key() -> None:
    # scenario: ctx-unbound-key
    assert ConfigClient(ENV).get("PATH") is None
    assert ConfigClient(ENV).get("FUNCD_INVOKE_SOCKET") is None


def test_ctx_lake_get() -> None:
    # scenario: ctx-lake-get
    lake = LakeClient(ENV)
    assert lake.get("lake") == {"url": "127.0.0.1:9100", "token": "tok-a"}
    with pytest.raises(KeyError, match='"other"'):
        lake.get("other")


@pytest.mark.parametrize("lists", [{}, {"FUNCD_CONFIG_KEYS": ""}])
def test_an_absent_or_empty_key_list_is_no_keys(lists: dict[str, str]) -> None:
    env = {**lists, "LOG_LEVEL": "debug"}
    assert ConfigClient(env).list() == []
    assert ConfigClient(env).get("LOG_LEVEL") is None


def test_the_clients_read_os_environ_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    config, lake = ConfigClient(), LakeClient()
    for var in ("FUNCD_LAKE_LAKE_URL", "FUNCD_LAKE_LAKE_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FUNCD_CONFIG_KEYS", "A")
    monkeypatch.setenv("A", "1")
    assert config.get("A") == "1"
    with pytest.raises(KeyError):
        lake.get("lake")
    monkeypatch.setenv("A", "2")
    monkeypatch.setenv("FUNCD_LAKE_LAKE_URL", "127.0.0.1:9100")
    monkeypatch.setenv("FUNCD_LAKE_LAKE_TOKEN", "tok")
    assert config.get("A") == "2"
    assert lake.get("lake") == {"url": "127.0.0.1:9100", "token": "tok"}
