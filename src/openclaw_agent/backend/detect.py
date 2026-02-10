"""Auto-detect and create the appropriate backend."""

import logging

from .base import AgentBackend
from ..config import BackendConfig

logger = logging.getLogger(__name__)


def create_backend(config: BackendConfig) -> AgentBackend:
    """Create a backend based on config type or auto-detection."""
    backend_type = config.type

    if backend_type == "auto":
        backend_type = _auto_detect(config)
        logger.info(f"Auto-detected backend: {backend_type}")

    if backend_type == "openclaw":
        from .openclaw_gateway import OpenClawGatewayBackend
        return OpenClawGatewayBackend(config.openclaw)
    elif backend_type == "openai":
        from .openai_direct import OpenAIDirectBackend
        return OpenAIDirectBackend(config.openai)
    elif backend_type == "langgraph":
        from .langgraph import LangGraphBackend
        return LangGraphBackend(config.langgraph)
    elif backend_type == "autogen":
        from .autogen import AutoGenBackend
        return AutoGenBackend(config.autogen)
    elif backend_type == "crewai":
        from .crewai import CrewAIBackend
        return CrewAIBackend(config.crewai)
    else:
        raise ValueError(f"Unknown backend type: {backend_type}")


def _auto_detect(config: BackendConfig) -> str:
    """Try to detect which backend is available based on config."""
    # OpenClaw Gateway takes highest priority
    if config.openclaw.gateway_url and config.openclaw.token:
        return "openclaw"

    if config.langgraph.base_url != "http://localhost:8000" or config.langgraph.runnable:
        return "langgraph"

    if config.autogen.base_url != "http://localhost:8000":
        return "autogen"

    if config.crewai.base_url != "http://localhost:8000" or config.crewai.bearer_token:
        return "crewai"

    if config.openai.api_key:
        return "openai"

    raise ValueError(
        "Cannot auto-detect backend. Set backend.type in config or provide OPENAI_API_KEY."
    )
