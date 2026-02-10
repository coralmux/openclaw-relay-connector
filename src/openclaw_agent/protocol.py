"""Relay protocol message types and helpers."""

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

PROTOCOL_VERSION = 1

# Message types
TYPE_AUTH = "auth"
TYPE_AUTH_OK = "auth.ok"
TYPE_AUTH_FAIL = "auth.fail"
TYPE_CHAT_SEND = "chat.send"
TYPE_CHAT_STREAM = "chat.stream"
TYPE_CHAT_DONE = "chat.done"
TYPE_CHAT_ERROR = "chat.error"
TYPE_PING = "ping"
TYPE_PONG = "pong"
TYPE_STATUS = "status"
TYPE_KEY_EXCHANGE = "key_exchange"

# Agent management types
TYPE_AGENT_LIST = "agent.list"
TYPE_AGENT_LIST_RESULT = "agent.list.result"
TYPE_AGENT_CREATE = "agent.create"
TYPE_AGENT_UPDATE = "agent.update"
TYPE_AGENT_DELETE = "agent.delete"
TYPE_AGENT_RESULT = "agent.result"

# Chat history types
TYPE_CHAT_HISTORY = "chat.history"
TYPE_CHAT_HISTORY_RESULT = "chat.history.result"

# Tool status
TYPE_CHAT_TOOL_STATUS = "chat.tool_status"

# Memory search
TYPE_MEMORY_SEARCH = "memory.search"
TYPE_MEMORY_SEARCH_RESULT = "memory.search.result"

# Group chat
TYPE_GROUP_LIST = "group.list"
TYPE_GROUP_LIST_RESULT = "group.list.result"
TYPE_GROUP_MESSAGES = "group.messages"
TYPE_GROUP_MESSAGES_RESULT = "group.messages.result"
TYPE_GROUP_SEND = "group.send"
TYPE_GROUP_MESSAGE = "group.message"

# System monitoring
TYPE_SYSTEM_STATUS = "system.status"
TYPE_SYSTEM_STATUS_RESULT = "system.status.result"


@dataclass
class Envelope:
    v: int
    type: str
    id: str
    ts: int
    payload: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "Envelope":
        d = json.loads(data)
        msg_type = d.get("type")
        if not msg_type:
            raise ValueError("Envelope missing required 'type' field")
        return cls(
            v=d.get("v", PROTOCOL_VERSION),
            type=msg_type,
            id=d.get("id", ""),
            ts=d.get("ts", 0),
            payload=d.get("payload", {}),
        )


def make_envelope(msg_type: str, payload: dict | None = None) -> Envelope:
    return Envelope(
        v=PROTOCOL_VERSION,
        type=msg_type,
        id=str(uuid.uuid4()),
        ts=int(time.time() * 1000),
        payload=payload or {},
    )


def auth_message(token: str, role: str = "agent") -> str:
    env = make_envelope(TYPE_AUTH, {"token": token, "role": role})
    return env.to_json()


def pong_message() -> str:
    return make_envelope(TYPE_PONG).to_json()


def chat_stream_message(delta: str, seq: int) -> str:
    env = make_envelope(TYPE_CHAT_STREAM, {"delta": delta, "seq": seq})
    return env.to_json()


def chat_done_message(full_text: str, usage: dict | None = None,
                      attachments: list[dict] | None = None) -> str:
    payload: dict[str, Any] = {"full_text": full_text}
    if usage:
        payload["usage"] = usage
    if attachments:
        payload["attachments"] = attachments
    env = make_envelope(TYPE_CHAT_DONE, payload)
    return env.to_json()


def chat_error_message(code: str, message: str) -> str:
    env = make_envelope(TYPE_CHAT_ERROR, {"code": code, "message": message})
    return env.to_json()


def key_exchange_message(pubkey_b64: str) -> str:
    env = make_envelope(TYPE_KEY_EXCHANGE, {"pubkey": pubkey_b64})
    return env.to_json()


def agent_list_result_message(agents: list[dict]) -> str:
    env = make_envelope(TYPE_AGENT_LIST_RESULT, {"agents": agents})
    return env.to_json()


def agent_result_message(ok: bool, action: str,
                         agent: dict | None = None,
                         error: str | None = None) -> str:
    payload: dict[str, Any] = {"ok": ok, "action": action}
    if agent is not None:
        payload["agent"] = agent
    if error is not None:
        payload["error"] = error
    env = make_envelope(TYPE_AGENT_RESULT, payload)
    return env.to_json()


def chat_history_result_message(messages: list[dict]) -> str:
    env = make_envelope(TYPE_CHAT_HISTORY_RESULT, {"messages": messages})
    return env.to_json()


def chat_tool_status_message(tool: str, status: str) -> str:
    env = make_envelope(TYPE_CHAT_TOOL_STATUS, {"tool": tool, "status": status})
    return env.to_json()


def memory_search_result_message(memories: list[dict]) -> str:
    env = make_envelope(TYPE_MEMORY_SEARCH_RESULT, {"memories": memories})
    return env.to_json()


# Group chat helpers
def group_list_result_message(rooms: list[dict]) -> str:
    env = make_envelope(TYPE_GROUP_LIST_RESULT, {"rooms": rooms})
    return env.to_json()


def group_messages_result_message(room_id: str, messages: list[dict]) -> str:
    env = make_envelope(TYPE_GROUP_MESSAGES_RESULT, {"room_id": room_id, "messages": messages})
    return env.to_json()


def group_message_event(room_id: str, message: dict) -> str:
    env = make_envelope(TYPE_GROUP_MESSAGE, {"room_id": room_id, "message": message})
    return env.to_json()


# System monitoring helpers
def system_status_result_message(status: dict) -> str:
    env = make_envelope(TYPE_SYSTEM_STATUS_RESULT, status)
    return env.to_json()
