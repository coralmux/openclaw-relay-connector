"""Abstract base class for AI agent backends."""

from abc import ABC, abstractmethod
from typing import AsyncIterator


class AgentBackend(ABC):
    """Base class for all AI backends that process chat messages."""

    @abstractmethod
    async def process_message(
        self,
        text: str,
        conversation: list[dict],
        attachments: list[dict] | None = None,
        model: str = "",
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        """Process a chat message and yield streaming text deltas.

        Args:
            text: The user's message text.
            conversation: Previous conversation messages [{role, content}, ...].
            attachments: Optional list of attachments [{type, mime_type, content_b64}].
            model: Model name to use (backend-specific).
            options: Additional options (temperature, system_prompt, etc.).

        Yields:
            Text delta strings as they become available.
        """
        ...
