"""Tests for configuration validation and loading."""

import os
from pathlib import Path

import pytest
from openclaw_agent.config import (
    AgentConfig,
    RelayConfig,
    BackendConfig,
    OpenAIConfig,
    load_config,
    validate_config,
    _expand_env,
    VALID_BACKEND_TYPES,
)


def test_default_config():
    """Test default configuration values."""
    config = AgentConfig()
    assert config.relay.url == "wss://relay.openclaw.dev/ws"
    assert config.relay.token == ""
    assert config.backend.type == "auto"


def test_validate_valid_config():
    config = AgentConfig()
    config.relay.token = "oc_pair_test123"
    warnings = validate_config(config)
    # No warnings expected for valid config
    assert all("relay.url" not in w for w in warnings)


def test_validate_missing_token():
    config = AgentConfig()
    config.relay.token = ""
    warnings = validate_config(config)
    assert any("relay.token" in w for w in warnings)


def test_validate_invalid_backend_type():
    config = AgentConfig()
    config.relay.token = "test"
    config.backend.type = "invalid_backend"
    warnings = validate_config(config)
    assert any("backend.type" in w for w in warnings)


def test_validate_openai_missing_api_key():
    config = AgentConfig()
    config.relay.token = "test"
    config.backend.type = "openai"
    config.backend.openai.api_key = ""
    warnings = validate_config(config)
    assert any("api_key" in w for w in warnings)


def test_validate_relay_url_scheme():
    config = AgentConfig()
    config.relay.token = "test"
    config.relay.url = "http://example.com/ws"  # Wrong scheme
    warnings = validate_config(config)
    assert any("ws://" in w for w in warnings)


def test_expand_env(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "hello")
    result = _expand_env("${TEST_VAR}_world")
    assert result == "hello_world"


def test_expand_env_missing(monkeypatch):
    monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
    result = _expand_env("${NONEXISTENT_VAR}")
    assert result == ""


def test_expand_env_no_expansion():
    result = _expand_env("plain string")
    assert result == "plain string"


def test_load_config_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
relay:
  url: wss://custom.relay.dev/ws
  token: my_token
backend:
  type: openai
  openai:
    api_key: sk-test
    model: gpt-4
""")
    config = load_config(path=config_file)
    assert config.relay.url == "wss://custom.relay.dev/ws"
    assert config.relay.token == "my_token"
    assert config.backend.type == "openai"
    assert config.backend.openai.api_key == "sk-test"
    assert config.backend.openai.model == "gpt-4"


def test_load_config_cli_overrides(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("""
relay:
  url: wss://file.relay.dev/ws
  token: file_token
backend:
  type: openai
""")
    config = load_config(
        path=config_file,
        token="cli_token",
        relay_url="wss://cli.relay.dev/ws",
        backend_type="langgraph",
    )
    assert config.relay.token == "cli_token"
    assert config.relay.url == "wss://cli.relay.dev/ws"
    assert config.backend.type == "langgraph"


def test_load_config_env_fallback(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("relay:\n  url: wss://relay.dev/ws\n")

    monkeypatch.setenv("OPENCLAW_RELAY_TOKEN", "env_token")
    monkeypatch.setenv("OPENAI_API_KEY", "env_api_key")

    config = load_config(path=config_file)
    assert config.relay.token == "env_token"
    assert config.backend.openai.api_key == "env_api_key"


def test_load_config_nonexistent_file():
    config = load_config(path=Path("/nonexistent/path/config.yaml"))
    assert config.relay.url == "wss://relay.openclaw.dev/ws"


def test_valid_backend_types():
    assert "auto" in VALID_BACKEND_TYPES
    assert "openai" in VALID_BACKEND_TYPES
    assert "openclaw" in VALID_BACKEND_TYPES
    assert "langgraph" in VALID_BACKEND_TYPES
    assert "autogen" in VALID_BACKEND_TYPES
    assert "crewai" in VALID_BACKEND_TYPES
