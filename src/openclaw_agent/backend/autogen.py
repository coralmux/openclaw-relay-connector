"""AutoGen FastAPI backend via HTTP SSE."""

import json
import logging
from typing import AsyncIterator

import httpx

from .base import AgentBackend
from ..config import AutoGenConfig

logger = logging.getLogger(__name__)


class AutoGenBackend(AgentBackend):
    """Calls an AutoGen FastAPI endpoint with SSE streaming."""

    def __init__(self, config: AutoGenConfig):
        self.config = config

    async def process_message(
        self,
        text: str,
        conversation: list[dict],
        attachments: list[dict] | None = None,
        model: str = "",
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        base_url = self.config.base_url.rstrip("/")
        url = f"{base_url}/{self.config.endpoint}"

        payload = {
            "message": text,
            "history": conversation,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError as e:
                            logger.warning(f"SSE parse error: {e}, line: {line[:100]}")
                            continue
                        content = data.get("content", data.get("delta", ""))
                        if isinstance(content, str) and content:
                            yield content
