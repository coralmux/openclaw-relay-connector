"""LangGraph / LangServe backend via HTTP SSE."""

import json
import logging
from typing import AsyncIterator

import httpx

from .base import AgentBackend
from ..config import LangGraphConfig

logger = logging.getLogger(__name__)


class LangGraphBackend(AgentBackend):
    """Calls a LangServe deployment via SSE streaming."""

    def __init__(self, config: LangGraphConfig):
        self.config = config

    async def process_message(
        self,
        text: str,
        conversation: list[dict],
        attachments: list[dict] | None = None,
        model: str = "",
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        options = options or {}
        base_url = self.config.base_url.rstrip("/")
        runnable = self.config.runnable

        url = f"{base_url}/{runnable}/stream" if runnable else f"{base_url}/stream"

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key

        payload = {
            "input": {
                "messages": [
                    *[{"role": m["role"], "content": m["content"]} for m in conversation],
                    {"role": "user", "content": text},
                ]
            }
        }

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError as e:
                            logger.warning(f"SSE parse error: {e}, line: {line[:100]}")
                            continue
                        if isinstance(data, dict):
                            content = data.get("content", data.get("output", ""))
                            if isinstance(content, str) and content:
                                yield content
