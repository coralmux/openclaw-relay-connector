"""Tests for protocol message types and helpers."""

import json

import pytest
from openclaw_agent.protocol import (
    Envelope,
    make_envelope,
    auth_message,
    pong_message,
    chat_stream_message,
    chat_done_message,
    chat_error_message,
    key_exchange_message,
    agent_list_result_message,
    agent_result_message,
    PROTOCOL_VERSION,
    TYPE_AUTH,
    TYPE_PONG,
    TYPE_CHAT_STREAM,
    TYPE_CHAT_DONE,
    TYPE_CHAT_ERROR,
    TYPE_KEY_EXCHANGE,
    TYPE_AGENT_LIST_RESULT,
    TYPE_AGENT_RESULT,
)


def test_envelope_to_json():
    env = make_envelope("test.type", {"key": "value"})
    json_str = env.to_json()
    parsed = json.loads(json_str)
    assert parsed["type"] == "test.type"
    assert parsed["v"] == PROTOCOL_VERSION
    assert parsed["payload"]["key"] == "value"
    assert "id" in parsed
    assert "ts" in parsed


def test_envelope_from_json():
    data = json.dumps({
        "v": 1,
        "type": "chat.send",
        "id": "test-id",
        "ts": 1234567890,
        "payload": {"text": "hello"}
    })
    env = Envelope.from_json(data)
    assert env.type == "chat.send"
    assert env.id == "test-id"
    assert env.payload["text"] == "hello"


def test_envelope_from_json_missing_type():
    """Test that missing 'type' field raises ValueError."""
    data = json.dumps({"v": 1, "id": "test", "payload": {}})
    with pytest.raises(ValueError, match="type"):
        Envelope.from_json(data)


def test_envelope_from_json_defaults():
    """Test that missing optional fields get defaults."""
    data = json.dumps({"type": "ping"})
    env = Envelope.from_json(data)
    assert env.v == PROTOCOL_VERSION
    assert env.id == ""
    assert env.ts == 0
    assert env.payload == {}


def test_envelope_from_json_invalid():
    with pytest.raises(json.JSONDecodeError):
        Envelope.from_json("not json")


def test_auth_message():
    msg = auth_message("my-token")
    parsed = json.loads(msg)
    assert parsed["type"] == TYPE_AUTH
    assert parsed["payload"]["token"] == "my-token"
    assert parsed["payload"]["role"] == "agent"


def test_auth_message_custom_role():
    msg = auth_message("token", role="phone")
    parsed = json.loads(msg)
    assert parsed["payload"]["role"] == "phone"


def test_pong_message():
    msg = pong_message()
    parsed = json.loads(msg)
    assert parsed["type"] == TYPE_PONG


def test_chat_stream_message():
    msg = chat_stream_message("hello ", 1)
    parsed = json.loads(msg)
    assert parsed["type"] == TYPE_CHAT_STREAM
    assert parsed["payload"]["delta"] == "hello "
    assert parsed["payload"]["seq"] == 1


def test_chat_done_message():
    msg = chat_done_message("full text here")
    parsed = json.loads(msg)
    assert parsed["type"] == TYPE_CHAT_DONE
    assert parsed["payload"]["full_text"] == "full text here"


def test_chat_done_message_with_usage():
    usage = {"prompt_tokens": 10, "completion_tokens": 20}
    msg = chat_done_message("text", usage=usage)
    parsed = json.loads(msg)
    assert parsed["payload"]["usage"]["prompt_tokens"] == 10


def test_chat_error_message():
    msg = chat_error_message("BACKEND_ERROR", "Something went wrong")
    parsed = json.loads(msg)
    assert parsed["type"] == TYPE_CHAT_ERROR
    assert parsed["payload"]["code"] == "BACKEND_ERROR"
    assert parsed["payload"]["message"] == "Something went wrong"


def test_key_exchange_message():
    msg = key_exchange_message("base64pubkey==")
    parsed = json.loads(msg)
    assert parsed["type"] == TYPE_KEY_EXCHANGE
    assert parsed["payload"]["pubkey"] == "base64pubkey=="


def test_agent_list_result_message():
    agents = [{"id": "1", "name": "Agent1"}, {"id": "2", "name": "Agent2"}]
    msg = agent_list_result_message(agents)
    parsed = json.loads(msg)
    assert parsed["type"] == TYPE_AGENT_LIST_RESULT
    assert len(parsed["payload"]["agents"]) == 2


def test_agent_result_message_success():
    msg = agent_result_message(True, "create", agent={"id": "1", "name": "New"})
    parsed = json.loads(msg)
    assert parsed["type"] == TYPE_AGENT_RESULT
    assert parsed["payload"]["ok"] is True
    assert parsed["payload"]["action"] == "create"
    assert parsed["payload"]["agent"]["id"] == "1"


def test_agent_result_message_failure():
    msg = agent_result_message(False, "delete", error="Agent not found")
    parsed = json.loads(msg)
    assert parsed["payload"]["ok"] is False
    assert parsed["payload"]["error"] == "Agent not found"


def test_make_envelope_no_payload():
    env = make_envelope("ping")
    assert env.type == "ping"
    assert env.payload == {}


def test_unicode_in_messages():
    msg = chat_stream_message("한글 테스트 🎉", 1)
    parsed = json.loads(msg)
    assert parsed["payload"]["delta"] == "한글 테스트 🎉"
