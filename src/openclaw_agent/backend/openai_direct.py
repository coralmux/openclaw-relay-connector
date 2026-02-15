"""OpenAI API direct backend."""

import logging
from typing import AsyncIterator

try:
    from openai import AsyncOpenAI
except ImportError:
    raise ImportError(
        "OpenAI backend requires the 'openai' package. "
        "Install it with: pip install openclaw-relay-connector[openai]"
    )

from .base import AgentBackend
from ..config import OpenAIConfig

logger = logging.getLogger(__name__)


class OpenAIDirectBackend(AgentBackend):
    """Calls OpenAI API directly with streaming."""

    def __init__(self, config: OpenAIConfig):
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def process_message(
        self,
        text: str,
        conversation: list[dict],
        attachments: list[dict] | None = None,
        model: str = "",
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        options = options or {}
        model = model or self.config.model

        messages = []

        # System prompt
        system_prompt = options.get("system_prompt")
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Conversation history (text only)
        for msg in conversation:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # Current message with optional image
        if attachments:
            content = [{"type": "text", "text": text}]
            for att in attachments:
                if att.get("type") == "image":
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{att['mime_type']};base64,{att['content_b64']}"
                        },
                    })
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": text})

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if "temperature" in options:
            kwargs["temperature"] = options["temperature"]
        if "max_tokens" in options:
            kwargs["max_tokens"] = options["max_tokens"]

        stream = await self.client.chat.completions.create(**kwargs)

        try:
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            raise
