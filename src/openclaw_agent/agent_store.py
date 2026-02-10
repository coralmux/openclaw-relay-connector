"""Agent profile storage with CRUD and JSON persistence."""

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentProfile:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    model: str = "gpt-4o"
    system_prompt: str = ""
    temperature: float = 0.7
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentProfile":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", ""),
            model=data.get("model", "gpt-4o"),
            system_prompt=data.get("system_prompt", ""),
            temperature=data.get("temperature", 0.7),
            created_at=data.get("created_at", int(time.time() * 1000)),
            updated_at=data.get("updated_at", int(time.time() * 1000)),
        )


DEFAULT_STORE_PATH = Path.home() / ".openclaw-agent" / "agents.json"


class AgentStore:
    """Manages agent profiles with JSON file persistence."""

    def __init__(self, path: Path = DEFAULT_STORE_PATH):
        self._path = path
        self._agents: dict[str, AgentProfile] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for item in data:
                    agent = AgentProfile.from_dict(item)
                    self._agents[agent.id] = agent
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to load agent store: {e}")
                self._agents = {}

    def _save(self):
        """Atomic write: write to temp file then rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = [a.to_dict() for a in self._agents.values()]
        content = json.dumps(data, ensure_ascii=False, indent=2)

        # Write to temp file in the same directory, then atomically rename
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent),
            suffix=".tmp",
            prefix=".agents_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, str(self._path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list(self) -> list[AgentProfile]:
        with self._lock:
            return list(self._agents.values())

    def get(self, agent_id: str) -> Optional[AgentProfile]:
        with self._lock:
            return self._agents.get(agent_id)

    def create(self, name: str, model: str = "gpt-4o",
               system_prompt: str = "", temperature: float = 0.7) -> AgentProfile:
        agent = AgentProfile(
            name=name,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
        )
        with self._lock:
            self._agents[agent.id] = agent
            self._save()
        return agent

    def update(self, agent_id: str, **kwargs) -> Optional[AgentProfile]:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return None
            for key in ("name", "model", "system_prompt", "temperature"):
                if key in kwargs and kwargs[key] is not None:
                    setattr(agent, key, kwargs[key])
            agent.updated_at = int(time.time() * 1000)
            self._save()
            return agent

    def delete(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id in self._agents:
                del self._agents[agent_id]
                self._save()
                return True
            return False

    def ensure_default(self) -> AgentProfile:
        """Create a default agent if none exist. Returns the first agent."""
        with self._lock:
            if not self._agents:
                agent = AgentProfile(
                    name="Assistant",
                    model="gpt-4o",
                    system_prompt="You are a helpful assistant.",
                    temperature=0.7,
                )
                self._agents[agent.id] = agent
                self._save()
                return agent
            return next(iter(self._agents.values()))
