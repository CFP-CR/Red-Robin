"""API key env-var-only loading — refuse keys in config.toml (spec §6 T2)."""

import pytest

from redrobin.infrastructure.llm.api_keys import (
    get_anthropic_key,
    get_voyage_key,
    refuse_api_key_in_config,
)


def test_get_anthropic_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert get_anthropic_key() == "sk-ant-test"


def test_get_anthropic_key_missing_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        get_anthropic_key()


def test_get_voyage_key_from_env(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "pa-test")
    assert get_voyage_key() == "pa-test"


def test_get_voyage_key_missing_raises(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY"):
        get_voyage_key()


def test_refuse_api_key_in_config_anthropic():
    with pytest.raises(ValueError, match="api_key"):
        refuse_api_key_in_config({"llm": {"api_key": "sk-ant-leak"}})


def test_refuse_api_key_in_config_voyage():
    with pytest.raises(ValueError, match="api_key"):
        refuse_api_key_in_config({"voyage": {"api_key": "pa-leak"}})


def test_refuse_api_key_in_config_passes_clean_config():
    refuse_api_key_in_config({"llm": {"model": "claude-sonnet-4-6"}})  # no raise
    refuse_api_key_in_config({})  # empty config also passes
