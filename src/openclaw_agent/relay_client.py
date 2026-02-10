"""WebSocket client for connecting to the relay server."""

import asyncio
import json
import logging
import time
from typing import Optional

import websockets
from websockets.exceptions import ConnectionClosed

from .agent_store import AgentStore
from .backend import AgentBackend
from .backend.openclaw_gateway import OpenClawGatewayBackend
from .e2e_crypto import E2ECrypto
from .protocol import (
    Envelope,
    TYPE_AUTH_OK,
    TYPE_AUTH_FAIL,
    TYPE_CHAT_SEND,
    TYPE_KEY_EXCHANGE,
    TYPE_PING,
    TYPE_STATUS,
    TYPE_AGENT_LIST,
    TYPE_AGENT_CREATE,
    TYPE_AGENT_UPDATE,
    TYPE_AGENT_DELETE,
    TYPE_CHAT_HISTORY,
    TYPE_MEMORY_SEARCH,
    TYPE_GROUP_LIST,
    TYPE_GROUP_MESSAGES,
    TYPE_GROUP_SEND,
    TYPE_SYSTEM_STATUS,
    auth_message,
    pong_message,
    key_exchange_message,
    chat_stream_message,
    chat_done_message,
    chat_error_message,
    agent_list_result_message,
    agent_result_message,
    chat_history_result_message,
    chat_tool_status_message,
    memory_search_result_message,
    group_list_result_message,
    group_messages_result_message,
    system_status_result_message,
    make_envelope,
    TYPE_CHAT_STREAM,
    TYPE_CHAT_DONE,
    TYPE_CHAT_ERROR,
)

logger = logging.getLogger(__name__)

RECONNECT_DELAY = 5
MAX_RECONNECT_DELAY = 60
MESSAGE_LOOP_TIMEOUT = 300  # 5 minutes max idle before reconnect


class RelayClient:
    """Manages the WebSocket connection to the relay and dispatches messages."""

    def __init__(self, relay_url: str, token: str, backend: AgentBackend,
                 agent_store: Optional[AgentStore] = None,
                 api_base_url: Optional[str] = None):
        self.relay_url = relay_url
        self.token = token
        self.backend = backend
        self.agent_store = agent_store
        self.api_base_url = api_base_url  # For group chat, monitoring, etc.
        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._crypto: Optional[E2ECrypto] = None
        self._crypto_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    async def run(self):
        """Main loop: connect, authenticate, process messages, reconnect on failure."""
        self._running = True
        delay = RECONNECT_DELAY

        while self._running:
            try:
                logger.info(f"Connecting to relay: {self.relay_url}")
                async with websockets.connect(
                    self.relay_url,
                    max_size=5 * 1024 * 1024,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    delay = RECONNECT_DELAY

                    # Fresh keypair per connection
                    async with self._crypto_lock:
                        self._crypto = E2ECrypto()

                    # Authenticate
                    await ws.send(auth_message(self.token))
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    env = Envelope.from_json(raw)

                    if env.type == TYPE_AUTH_FAIL:
                        logger.error(f"Auth failed: {env.payload}")
                        self._running = False
                        return

                    if env.type == TYPE_AUTH_OK:
                        paired = env.payload.get("paired", False)
                        logger.info(f"Authenticated. Paired: {paired}")

                        # Send our public key to peer
                        async with self._crypto_lock:
                            if self._crypto:
                                await ws.send(key_exchange_message(self._crypto.public_key_b64))
                        logger.info("Sent public key for E2E encryption")

                    # Message loop
                    await self._message_loop(ws)

            except ConnectionClosed as e:
                logger.warning(f"Connection closed: {e}")
            except asyncio.TimeoutError:
                logger.warning("Connection timeout")
            except Exception as e:
                logger.error(f"Connection error: {e}")
            finally:
                self._ws = None
                async with self._crypto_lock:
                    self._crypto = None
                # Cancel any pending tasks
                await self._cancel_tasks()

            if self._running:
                logger.info(f"Reconnecting in {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)

    async def _message_loop(self, ws: websockets.WebSocketClientProtocol):
        """Process incoming messages from the relay."""
        async for raw in ws:
            try:
                env = Envelope.from_json(raw)

                if env.type == TYPE_PING:
                    await ws.send(pong_message())

                elif env.type == TYPE_KEY_EXCHANGE:
                    peer_pubkey = env.payload.get("pubkey", "")
                    if peer_pubkey:
                        async with self._crypto_lock:
                            if self._crypto:
                                try:
                                    self._crypto.derive_shared_key(peer_pubkey)
                                    logger.info("E2E encryption established")
                                except Exception as e:
                                    logger.error(f"Key derivation failed: {e}")

                elif env.type == TYPE_CHAT_SEND:
                    task = asyncio.create_task(self._handle_chat(ws, env))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

                elif env.type == TYPE_AGENT_LIST:
                    await self._handle_agent_list(ws)

                elif env.type == TYPE_AGENT_CREATE:
                    await self._handle_agent_create(ws, env)

                elif env.type == TYPE_AGENT_UPDATE:
                    await self._handle_agent_update(ws, env)

                elif env.type == TYPE_AGENT_DELETE:
                    await self._handle_agent_delete(ws, env)

                elif env.type == TYPE_CHAT_HISTORY:
                    await self._handle_chat_history(ws, env)

                elif env.type == TYPE_MEMORY_SEARCH:
                    await self._handle_memory_search(ws, env)

                elif env.type == TYPE_GROUP_LIST:
                    await self._handle_group_list(ws, env)

                elif env.type == TYPE_GROUP_MESSAGES:
                    await self._handle_group_messages(ws, env)

                elif env.type == TYPE_GROUP_SEND:
                    await self._handle_group_send(ws, env)

                elif env.type == TYPE_SYSTEM_STATUS:
                    await self._handle_system_status(ws, env)

                elif env.type == TYPE_STATUS:
                    peer = env.payload.get("peer", "unknown")
                    logger.info(f"Peer status: {peer}")
                    # Peer reconnected - send our key again
                    if peer == "online":
                        async with self._crypto_lock:
                            self._crypto = E2ECrypto()
                            await ws.send(key_exchange_message(self._crypto.public_key_b64))
                        logger.info("Re-sent public key after peer reconnect")

            except Exception as e:
                logger.error(f"Error processing message: {e}")

    async def _handle_chat(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        """Handle a chat.send message by invoking the backend."""
        payload = env.payload

        # Decrypt if E2E is active
        async with self._crypto_lock:
            crypto = self._crypto
        if payload.get("enc") and crypto and crypto.is_ready:
            try:
                payload = crypto.decrypt_payload(payload)
            except Exception as e:
                logger.error(f"Decryption failed: {e}")
                await ws.send(chat_error_message("DECRYPTION_FAILED", str(e)))
                return

        text = payload.get("text", "")
        conversation = payload.get("conversation", [])
        attachments = payload.get("attachments")
        model = payload.get("model", "")
        options = payload.get("options") or {}

        # Apply agent profile overrides when agent_id is provided
        agent_id = payload.get("agent_id")
        if isinstance(self.backend, OpenClawGatewayBackend):
            # Map agent_id to OpenClaw session key
            oc_agent_id = agent_id or "main"
            options["session_key"] = f"agent:{oc_agent_id}:main"
        elif agent_id and self.agent_store:
            agent = self.agent_store.get(agent_id)
            if agent:
                if not model:
                    model = agent.model
                if "system_prompt" not in options and agent.system_prompt:
                    options["system_prompt"] = agent.system_prompt
                if "temperature" not in options:
                    options["temperature"] = agent.temperature

        seq = 0
        full_text = ""

        async with self._crypto_lock:
            crypto = self._crypto
        e2e = crypto and crypto.is_ready

        # Set tool status callback for gateway backend
        if isinstance(self.backend, OpenClawGatewayBackend):
            async def _on_tool_status(tool: str, status: str):
                try:
                    await ws.send(chat_tool_status_message(tool, status))
                except Exception:
                    pass
            self.backend._tool_status_callback = _on_tool_status

        try:
            async for delta in self.backend.process_message(
                text=text,
                conversation=conversation,
                attachments=attachments,
                model=model,
                options=options,
            ):
                seq += 1
                full_text += delta

                if e2e:
                    encrypted = crypto.encrypt_payload({"delta": delta, "seq": seq})
                    msg = make_envelope(TYPE_CHAT_STREAM, encrypted)
                    await ws.send(msg.to_json())
                else:
                    await ws.send(chat_stream_message(delta, seq))

            # Collect attachments (images) from gateway backend if available
            attachments = getattr(self.backend, '_last_attachments', None) or None

            if e2e:
                done_payload = {"full_text": full_text}
                if attachments:
                    done_payload["attachments"] = attachments
                encrypted = crypto.encrypt_payload(done_payload)
                msg = make_envelope(TYPE_CHAT_DONE, encrypted)
                await ws.send(msg.to_json())
            else:
                await ws.send(chat_done_message(full_text, attachments=attachments))

            logger.info(f"Chat completed: {len(full_text)} chars, {seq} chunks, "
                        f"{len(attachments) if attachments else 0} attachments (e2e={e2e})")

        except Exception as e:
            logger.error(f"Backend error: {e}")
            # Send partial result if available, then error
            if full_text and seq > 0:
                try:
                    if e2e:
                        done_payload = {"full_text": full_text}
                        encrypted = crypto.encrypt_payload(done_payload)
                        msg = make_envelope(TYPE_CHAT_DONE, encrypted)
                        await ws.send(msg.to_json())
                    else:
                        await ws.send(chat_done_message(full_text))
                except Exception:
                    pass  # best effort
            await ws.send(chat_error_message("BACKEND_ERROR", str(e)))
        finally:
            if isinstance(self.backend, OpenClawGatewayBackend):
                self.backend._tool_status_callback = None

    async def _handle_agent_list(self, ws: websockets.WebSocketClientProtocol):
        if isinstance(self.backend, OpenClawGatewayBackend):
            try:
                gw_agents = await self.backend.list_agents()
                agents = [
                    {
                        "id": a["id"],
                        "name": a.get("identity", {}).get("name") or a.get("name") or a["id"],
                        "model": "",
                        "system_prompt": "",
                    }
                    for a in gw_agents
                ]
            except Exception as e:
                logger.error(f"Failed to list OpenClaw agents: {e}")
                agents = []
            await ws.send(agent_list_result_message(agents))
            logger.info(f"Sent OpenClaw agent list: {len(agents)} agents")
            return

        if not self.agent_store:
            await ws.send(agent_list_result_message([]))
            return
        agents = [a.to_dict() for a in self.agent_store.list()]
        await ws.send(agent_list_result_message(agents))
        logger.info(f"Sent agent list: {len(agents)} agents")

    async def _handle_chat_history(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        """Handle a chat.history request by querying the gateway."""
        if not isinstance(self.backend, OpenClawGatewayBackend):
            await ws.send(chat_history_result_message([]))
            return

        payload = env.payload
        agent_id = payload.get("agent_id", "main")
        limit = payload.get("limit", 100)
        session_key = f"agent:{agent_id}:main"

        try:
            messages = await self.backend.get_history(session_key, limit)
            await ws.send(chat_history_result_message(messages))
            logger.info(f"Sent chat history: {len(messages)} messages (agent={agent_id})")
        except Exception as e:
            logger.error(f"Failed to get chat history: {e}")
            await ws.send(chat_history_result_message([]))

    async def _handle_memory_search(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        """Handle a memory.search request by querying the gateway."""
        if not isinstance(self.backend, OpenClawGatewayBackend):
            await ws.send(memory_search_result_message([]))
            return

        payload = env.payload
        agent_id = payload.get("agent_id", "main")
        query = payload.get("query", "")
        limit = payload.get("limit", 20)
        session_key = f"agent:{agent_id}:main"

        try:
            memories = await self.backend.search_memory(session_key, query, limit)
            await ws.send(memory_search_result_message(memories))
            logger.info(f"Sent memory search results: {len(memories)} items (agent={agent_id}, query='{query}')")
        except Exception as e:
            logger.error(f"Failed to search memory: {e}")
            await ws.send(memory_search_result_message([]))

    async def _handle_agent_create(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        if isinstance(self.backend, OpenClawGatewayBackend):
            await ws.send(agent_result_message(
                False, "create", error="OpenClaw agents are managed via workspace config"))
            return
        if not self.agent_store:
            await ws.send(agent_result_message(False, "create", error="Agent store not available"))
            return
        p = env.payload
        try:
            agent = self.agent_store.create(
                name=p.get("name", ""),
                model=p.get("model", "gpt-4o"),
                system_prompt=p.get("system_prompt", ""),
                temperature=p.get("temperature", 0.7),
            )
            await ws.send(agent_result_message(True, "create", agent=agent.to_dict()))
            logger.info(f"Agent created: {agent.name} ({agent.id})")
        except Exception as e:
            await ws.send(agent_result_message(False, "create", error=str(e)))

    async def _handle_agent_update(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        if isinstance(self.backend, OpenClawGatewayBackend):
            await ws.send(agent_result_message(
                False, "update", error="OpenClaw agents are managed via workspace config"))
            return
        if not self.agent_store:
            await ws.send(agent_result_message(False, "update", error="Agent store not available"))
            return
        p = env.payload
        agent_id = p.get("id")
        if not agent_id:
            await ws.send(agent_result_message(False, "update", error="Missing agent id"))
            return
        kwargs = {}
        for key in ("name", "model", "system_prompt", "temperature"):
            if key in p:
                kwargs[key] = p[key]
        agent = self.agent_store.update(agent_id, **kwargs)
        if agent:
            await ws.send(agent_result_message(True, "update", agent=agent.to_dict()))
            logger.info(f"Agent updated: {agent.name} ({agent.id})")
        else:
            await ws.send(agent_result_message(False, "update", error="Agent not found"))

    async def _handle_agent_delete(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        if isinstance(self.backend, OpenClawGatewayBackend):
            await ws.send(agent_result_message(
                False, "delete", error="OpenClaw agents are managed via workspace config"))
            return
        if not self.agent_store:
            await ws.send(agent_result_message(False, "delete", error="Agent store not available"))
            return
        agent_id = env.payload.get("id")
        if not agent_id:
            await ws.send(agent_result_message(False, "delete", error="Missing agent id"))
            return
        if self.agent_store.delete(agent_id):
            await ws.send(agent_result_message(True, "delete"))
            logger.info(f"Agent deleted: {agent_id}")
        else:
            await ws.send(agent_result_message(False, "delete", error="Agent not found"))

    async def _cancel_tasks(self):
        """Cancel all tracked background tasks."""
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def stop(self):
        """Signal the client to stop."""
        self._running = False
        if self._ws:
            asyncio.create_task(self._ws.close())

    # ========== Group Chat Handlers ==========

    async def _handle_group_list(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        """Handle a group.list request - return list of chat rooms."""
        try:
            rooms = await self._get_group_rooms()
            await ws.send(group_list_result_message(rooms))
            logger.info(f"Sent group list: {len(rooms)} rooms")
        except Exception as e:
            logger.error(f"Failed to get group list: {e}")
            await ws.send(group_list_result_message([]))

    async def _handle_group_messages(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        """Handle a group.messages request - return messages in a room."""
        payload = env.payload
        room_id = payload.get("room_id", "")
        limit = payload.get("limit", 50)
        before = payload.get("before")

        try:
            messages = await self._get_group_messages(room_id, limit, before)
            await ws.send(group_messages_result_message(room_id, messages))
            logger.info(f"Sent group messages: {len(messages)} messages (room={room_id})")
        except Exception as e:
            logger.error(f"Failed to get group messages: {e}")
            await ws.send(group_messages_result_message(room_id, []))

    async def _handle_group_send(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        """Handle a group.send request - send message to a room."""
        payload = env.payload
        room_id = payload.get("room_id", "")
        text = payload.get("text", "")

        try:
            await self._send_group_message(room_id, text)
            logger.info(f"Sent group message to {room_id}")
        except Exception as e:
            logger.error(f"Failed to send group message: {e}")
            await ws.send(chat_error_message("GROUP_SEND_FAILED", str(e)))

    async def _get_group_rooms(self) -> list[dict]:
        """Get list of group chat rooms. Override or configure API endpoint."""
        # Default implementation - can be extended via config or subclass
        if hasattr(self, 'api_base_url') and self.api_base_url:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_base_url}/groups") as resp:
                    if resp.status == 200:
                        return await resp.json()
        return []

    async def _get_group_messages(self, room_id: str, limit: int, before: int | None) -> list[dict]:
        """Get messages in a group chat room."""
        if hasattr(self, 'api_base_url') and self.api_base_url:
            import aiohttp
            params = {"limit": limit}
            if before:
                params["before"] = before
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_base_url}/groups/{room_id}/messages", params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
        return []

    async def _send_group_message(self, room_id: str, text: str):
        """Send a message to a group chat room."""
        if hasattr(self, 'api_base_url') and self.api_base_url:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_base_url}/groups/{room_id}/messages",
                    json={"text": text}
                ) as resp:
                    if resp.status != 200:
                        raise Exception(f"Failed to send: {resp.status}")

    # ========== System Monitoring Handlers ==========

    async def _handle_system_status(self, ws: websockets.WebSocketClientProtocol, env: Envelope):
        """Handle a system.status request - return system metrics."""
        try:
            status = await self._get_system_status()
            await ws.send(system_status_result_message(status))
            logger.info(f"Sent system status")
        except Exception as e:
            logger.error(f"Failed to get system status: {e}")
            await ws.send(system_status_result_message({}))

    async def _get_system_status(self) -> dict:
        """Get system metrics. Uses psutil if available."""
        status = {}
        try:
            import psutil
            status["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            status["memory_percent"] = mem.percent
            status["memory_used_gb"] = round(mem.used / (1024**3), 1)
            status["memory_total_gb"] = round(mem.total / (1024**3), 1)
            status["uptime_seconds"] = int(time.time() - psutil.boot_time())
        except ImportError:
            logger.warning("psutil not available for system monitoring")
        
        # GPU metrics (macOS specific)
        try:
            import subprocess
            result = subprocess.run(
                ["sudo", "powermetrics", "-n", "1", "-i", "100", "--samplers", "gpu_power"],
                capture_output=True, text=True, timeout=2
            )
            if "GPU" in result.stdout:
                # Parse GPU usage from powermetrics output
                for line in result.stdout.split("\n"):
                    if "GPU Active" in line:
                        import re
                        match = re.search(r"(\d+\.?\d*)%", line)
                        if match:
                            status["gpu_percent"] = float(match.group(1))
                            break
        except Exception:
            pass  # GPU metrics optional
        
        return status
