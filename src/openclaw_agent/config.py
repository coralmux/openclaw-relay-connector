"""Configuration loading from YAML + environment variables."""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

DEFAULT_RELAY_URL = "wss://relay.coralmux.com/ws"
DEFAULT_CONFIG_PATH = Path.home() / ".openclaw-agent" / "config.yaml"

VALID_BACKEND_TYPES = {"auto", "openai", "langgraph", "autogen", "crewai", "openclaw"}


@dataclass
class RelayConfig:
    url: str = DEFAULT_RELAY_URL
    token: str = ""


@dataclass
class OpenAIConfig:
    api_key: str = ""
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"


@dataclass
class LangGraphConfig:
    base_url: str = "http://localhost:8000"
    runnable: str = ""
    api_key: str = ""


@dataclass
class AutoGenConfig:
    base_url: str = "http://localhost:8000"
    endpoint: str = "chat"


@dataclass
class CrewAIConfig:
    base_url: str = "http://localhost:8000"
    crew_name: str = ""
    bearer_token: str = ""


@dataclass
class OpenClawConfig:
    gateway_url: str = "ws://127.0.0.1:18789"
    token: str = ""


@dataclass
class ApiConfig:
    base_url: str = ""  # Extended API for group chat, monitoring, etc.


@dataclass
class BackendConfig:
    type: str = "auto"  # auto | openai | langgraph | autogen | crewai | openclaw
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)
    langgraph: LangGraphConfig = field(default_factory=LangGraphConfig)
    autogen: AutoGenConfig = field(default_factory=AutoGenConfig)
    crewai: CrewAIConfig = field(default_factory=CrewAIConfig)
    openclaw: OpenClawConfig = field(default_factory=OpenClawConfig)


@dataclass
class AgentConfig:
    relay: RelayConfig = field(default_factory=RelayConfig)
    backend: BackendConfig = field(default_factory=BackendConfig)
    api: ApiConfig = field(default_factory=ApiConfig)


def _expand_env(value: str) -> str:
    """Expand ${VAR} references in string values."""
    if isinstance(value, str) and "${" in value:
        def replacer(m: re.Match) -> str:
            var_name = m.group(1)
            val = os.environ.get(var_name, "")
            if not val:
                logger.warning(f"Environment variable ${{{var_name}}} is not set")
            return val
        return re.sub(r"\$\{(\w+)\}", replacer, value)
    return value


def _process_dict(d: dict) -> dict:
    """Recursively expand environment variables in dict values."""
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _process_dict(v)
        elif isinstance(v, str):
            result[k] = _expand_env(v)
        else:
            result[k] = v
    return result


def _validate_url(url: str, name: str) -> None:
    """Validate that a URL is well-formed."""
    if not url:
        return
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            logger.warning(f"Config: {name} URL may be malformed: {url}")
    except Exception:
        logger.warning(f"Config: {name} URL is invalid: {url}")


def validate_config(config: AgentConfig) -> list[str]:
    """Validate configuration and return list of warnings."""
    warnings = []

    # Validate relay URL
    _validate_url(config.relay.url, "relay.url")
    if not config.relay.url.startswith(("ws://", "wss://")):
        warnings.append(f"relay.url should start with ws:// or wss:// (got: {config.relay.url})")

    # Validate relay token
    if not config.relay.token:
        warnings.append("relay.token is empty — will attempt auto-registration")

    # Validate backend type
    if config.backend.type not in VALID_BACKEND_TYPES:
        warnings.append(f"backend.type '{config.backend.type}' is not valid. "
                        f"Must be one of: {', '.join(sorted(VALID_BACKEND_TYPES))}")

    # Validate backend-specific settings
    bt = config.backend.type
    if bt == "openai" and not config.backend.openai.api_key:
        warnings.append("backend.openai.api_key is required when backend.type is 'openai'")

    if bt == "openclaw":
        _validate_url(config.backend.openclaw.gateway_url, "backend.openclaw.gateway_url")
        if not config.backend.openclaw.token:
            warnings.append("backend.openclaw.token is empty")

    if bt == "langgraph":
        _validate_url(config.backend.langgraph.base_url, "backend.langgraph.base_url")

    if bt == "autogen":
        _validate_url(config.backend.autogen.base_url, "backend.autogen.base_url")

    if bt == "crewai":
        _validate_url(config.backend.crewai.base_url, "backend.crewai.base_url")

    # Validate OpenAI base_url if custom
    if config.backend.openai.base_url and config.backend.openai.base_url != "https://api.openai.com/v1":
        _validate_url(config.backend.openai.base_url, "backend.openai.base_url")

    return warnings


def load_config(
    path: Optional[Path] = None,
    token: Optional[str] = None,
    relay_url: Optional[str] = None,
    backend_type: Optional[str] = None,
) -> AgentConfig:
    """Load config from YAML file, with CLI overrides."""
    config = AgentConfig()

    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        raw = _process_dict(raw)

        relay = raw.get("relay", {})
        config.relay.url = relay.get("url", config.relay.url)
        config.relay.token = relay.get("token", config.relay.token)

        backend = raw.get("backend", {})
        config.backend.type = backend.get("type", config.backend.type)

        openai_cfg = backend.get("openai", {})
        if openai_cfg:
            config.backend.openai.api_key = openai_cfg.get("api_key", "")
            config.backend.openai.model = openai_cfg.get("model", "gpt-4o")
            config.backend.openai.base_url = openai_cfg.get("base_url", config.backend.openai.base_url)

        lg_cfg = backend.get("langgraph", {})
        if lg_cfg:
            config.backend.langgraph.base_url = lg_cfg.get("base_url", config.backend.langgraph.base_url)
            config.backend.langgraph.runnable = lg_cfg.get("runnable", "")
            config.backend.langgraph.api_key = lg_cfg.get("api_key", "")

        ag_cfg = backend.get("autogen", {})
        if ag_cfg:
            config.backend.autogen.base_url = ag_cfg.get("base_url", config.backend.autogen.base_url)
            config.backend.autogen.endpoint = ag_cfg.get("endpoint", "chat")

        crew_cfg = backend.get("crewai", {})
        if crew_cfg:
            config.backend.crewai.base_url = crew_cfg.get("base_url", config.backend.crewai.base_url)
            config.backend.crewai.crew_name = crew_cfg.get("crew_name", "")
            config.backend.crewai.bearer_token = crew_cfg.get("bearer_token", "")

        oc_cfg = backend.get("openclaw", {})
        if oc_cfg:
            config.backend.openclaw.gateway_url = oc_cfg.get("gateway_url", config.backend.openclaw.gateway_url)
            config.backend.openclaw.token = oc_cfg.get("token", "")

        # Extended API config
        api_cfg = raw.get("api", {})
        if api_cfg:
            config.api.base_url = api_cfg.get("base_url", "")

    # CLI overrides
    if token:
        config.relay.token = token
    if relay_url:
        config.relay.url = relay_url
    if backend_type:
        config.backend.type = backend_type

    # Environment variable fallbacks
    if not config.relay.token:
        config.relay.token = os.environ.get("OPENCLAW_RELAY_TOKEN", "")
    if not config.backend.openai.api_key:
        config.backend.openai.api_key = os.environ.get("OPENAI_API_KEY", "")
    if not config.backend.openclaw.token:
        config.backend.openclaw.token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")

    # Validate and log warnings
    warnings = validate_config(config)
    for w in warnings:
        logger.warning(f"Config warning: {w}")

    return config
