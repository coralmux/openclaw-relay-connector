"""Tests for AgentStore: CRUD, atomic write, and concurrent access."""

import json
import os
import threading
from pathlib import Path

import pytest
from openclaw_agent.agent_store import AgentStore, AgentProfile


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "agents.json"


@pytest.fixture
def store(store_path):
    return AgentStore(path=store_path)


def test_create_agent(store):
    agent = store.create(name="Test Agent", model="gpt-4o")
    assert agent.name == "Test Agent"
    assert agent.model == "gpt-4o"
    assert agent.id


def test_list_agents(store):
    assert len(store.list()) == 0
    store.create(name="A1")
    store.create(name="A2")
    assert len(store.list()) == 2


def test_get_agent(store):
    created = store.create(name="Findme")
    found = store.get(created.id)
    assert found is not None
    assert found.name == "Findme"


def test_get_nonexistent(store):
    assert store.get("nonexistent-id") is None


def test_update_agent(store):
    agent = store.create(name="Original")
    updated = store.update(agent.id, name="Updated", temperature=0.5)
    assert updated is not None
    assert updated.name == "Updated"
    assert updated.temperature == 0.5
    assert updated.updated_at >= agent.created_at


def test_update_nonexistent(store):
    result = store.update("nonexistent", name="Foo")
    assert result is None


def test_delete_agent(store):
    agent = store.create(name="ToDelete")
    assert store.delete(agent.id) is True
    assert store.get(agent.id) is None
    assert len(store.list()) == 0


def test_delete_nonexistent(store):
    assert store.delete("nonexistent") is False


def test_persistence(store_path):
    """Test that data survives store recreation."""
    store1 = AgentStore(path=store_path)
    store1.create(name="Persistent", model="gpt-4")

    store2 = AgentStore(path=store_path)
    agents = store2.list()
    assert len(agents) == 1
    assert agents[0].name == "Persistent"
    assert agents[0].model == "gpt-4"


def test_atomic_write(store_path):
    """Test that the store file is valid JSON after write."""
    store = AgentStore(path=store_path)
    store.create(name="Atomic")

    assert store_path.exists()
    data = json.loads(store_path.read_text())
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "Atomic"


def test_ensure_default(store):
    """Test ensure_default creates a default agent if none exist."""
    default = store.ensure_default()
    assert default.name == "Assistant"
    assert len(store.list()) == 1

    # Calling again should not create a new one
    second = store.ensure_default()
    assert second.id == default.id
    assert len(store.list()) == 1


def test_concurrent_access(store):
    """Test that concurrent operations don't corrupt state."""
    errors = []

    def worker(name):
        try:
            agent = store.create(name=name)
            store.update(agent.id, temperature=0.9)
            store.get(agent.id)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(f"Agent-{i}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(store.list()) == 20


def test_corrupt_file_recovery(store_path):
    """Test that a corrupt JSON file is handled gracefully."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("NOT VALID JSON!!!")

    store = AgentStore(path=store_path)
    assert len(store.list()) == 0  # Should recover with empty list


def test_agent_profile_to_dict():
    profile = AgentProfile(
        id="test-id",
        name="Test",
        model="gpt-4o",
        system_prompt="Be helpful",
        temperature=0.8,
    )
    d = profile.to_dict()
    assert d["id"] == "test-id"
    assert d["name"] == "Test"
    assert d["temperature"] == 0.8


def test_agent_profile_from_dict():
    d = {"id": "abc", "name": "FromDict", "model": "gpt-3.5", "temperature": 0.3}
    profile = AgentProfile.from_dict(d)
    assert profile.id == "abc"
    assert profile.name == "FromDict"
    assert profile.model == "gpt-3.5"
    assert profile.temperature == 0.3
