"""CrewAI backend via kickoff + polling."""

import asyncio
import logging
from typing import AsyncIterator

import httpx

from .base import AgentBackend
from ..config import CrewAIConfig

logger = logging.getLogger(__name__)

# Exponential backoff parameters
INITIAL_POLL_INTERVAL = 1.0
MAX_POLL_INTERVAL = 10.0
BACKOFF_FACTOR = 1.5
MAX_POLL_ATTEMPTS = 180  # ~10 minutes with backoff


class CrewAIBackend(AgentBackend):
    """Calls a CrewAI endpoint with kickoff and polls for result."""

    def __init__(self, config: CrewAIConfig):
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
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.bearer_token}",
        }

        payload = {"inputs": {self.config.crew_name or "topic": text}}

        async with httpx.AsyncClient(timeout=300) as client:
            # Kickoff
            resp = await client.post(f"{base_url}/kickoff", json=payload, headers=headers)
            resp.raise_for_status()
            kickoff_data = resp.json()
            kickoff_id = kickoff_data.get("kickoff_id", kickoff_data.get("id", ""))

            # Poll for result with exponential backoff
            poll_interval = INITIAL_POLL_INTERVAL
            for attempt in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(poll_interval)
                poll_interval = min(poll_interval * BACKOFF_FACTOR, MAX_POLL_INTERVAL)

                try:
                    status_resp = await client.get(
                        f"{base_url}/status/{kickoff_id}",
                        headers=headers,
                    )
                    status_resp.raise_for_status()
                    status_data = status_resp.json()
                except httpx.HTTPError as e:
                    logger.warning(f"CrewAI poll error (attempt {attempt + 1}): {e}")
                    continue

                state = status_data.get("status", "")
                if state in ("completed", "done", "finished"):
                    result = status_data.get("result", status_data.get("output", ""))
                    if isinstance(result, str):
                        yield result
                    return
                elif state in ("failed", "error"):
                    raise RuntimeError(f"CrewAI task failed: {status_data}")

            raise TimeoutError("CrewAI task did not complete within timeout")
