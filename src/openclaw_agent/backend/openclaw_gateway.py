"""OpenClaw Gateway WebSocket backend.

Connects to a local OpenClaw Gateway via WebSocket and bridges chat messages
through it, enabling SOUL.md, memory, tools, and browser automation.
"""

import asyncio
import json
import logging
import uuid
from typing import AsyncIterator

import websockets

from .base import AgentBackend
from ..config import OpenClawConfig

logger = logging.getLogger(__name__)

CLIENT_ID = "gateway-client"
CLIENT_VERSION = "0.1.0"

# Timeout for waiting on gateway responses
GATEWAY_RPC_TIMEOUT = 30  # seconds
GATEWAY_STREAM_TIMEOUT = 180  # seconds


def _extract_text_from_message(message: dict | None) -> str:
    """Extract text content from an OpenClaw chat event message field."""
    if not message:
        return ""
    # Message may contain a "content" string directly
    if isinstance(message.get("content"), str):
        return message["content"]
    # Or a "text" field
    if isinstance(message.get("text"), str):
        return message["text"]
    # Or content blocks: [{"type": "text", "text": "..."}]
    blocks = message.get("content")
    if isinstance(blocks, list):
        parts = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def _extract_images_from_message(message: dict | None) -> list[dict]:
    """Extract image attachments from an OpenClaw chat event message field.

    Returns a list of dicts: [{"type": "image", "mime_type": ..., "url": ...}]
    """
    if not message:
        return []
    blocks = message.get("content")
    if not isinstance(blocks, list):
        return []
    images: list[dict] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        source = block.get("source", {})
        url = source.get("url") or block.get("url") or ""
        if url:
            images.append({
                "type": "image",
                "mime_type": source.get("media_type", "image/png"),
                "url": url,
            })
        # base64 inline image
        data = source.get("data") or block.get("data") or ""
        if data and not url:
            images.append({
                "type": "image",
                "mime_type": source.get("media_type", "image/png"),
                "content_b64": data,
            })
    return images


class OpenClawGatewayBackend(AgentBackend):
    """Bridges chat messages through an OpenClaw Gateway WebSocket connection."""

    def __init__(self, config: OpenClawConfig):
        self.config = config
        self._url = config.gateway_url
        self._token = config.token
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._connected = False
        self._last_attachments: list[dict] = []
        self._tool_status_callback = None  # async callable(tool, status)
        self._ws_lock = asyncio.Lock()  # Prevent concurrent recv() calls

    async def _ensure_connected(self):
        """Reconnect to gateway if the WebSocket is dead."""
        if self._ws and self._connected:
            # Check if WS is still alive
            try:
                await self._ws.ping()
                return
            except Exception:
                logger.warning("Gateway WebSocket dead, reconnecting...")
                self._connected = False
                self._ws = None
        await self.connect()

    async def connect(self):
        """Connect to the Gateway WebSocket and perform the hello handshake."""
        logger.info(f"Connecting to OpenClaw Gateway: {self._url}")
        # Send Origin header matching the gateway host so origin check passes
        import urllib.parse
        parsed = urllib.parse.urlparse(self._url)
        origin = f"http://{parsed.hostname}:{parsed.port}" if parsed.port else f"http://{parsed.hostname}"
        self._ws = await websockets.connect(
            self._url,
            max_size=5 * 1024 * 1024,
            origin=origin,
        )

        # Send connect as RPC method (protocol v3)
        req_id = str(uuid.uuid4())
        connect_params = {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {
                "id": CLIENT_ID,
                "version": CLIENT_VERSION,
                "platform": "python",
                "mode": "webchat",
            },
            "role": "operator",
            "scopes": [],
            "caps": [],
            "auth": {"token": self._token} if self._token else {},
        }
        request = {
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": connect_params,
        }
        await self._ws.send(json.dumps(request))

        # Wait for connect response with timeout
        deadline = asyncio.get_event_loop().time() + GATEWAY_RPC_TIMEOUT
        async for raw in self._ws:
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("Gateway connect timeout")

            frame = json.loads(raw)

            # Handle connect.challenge event - just re-send connect request
            if frame.get("type") == "event" and frame.get("event") == "connect.challenge":
                await self._ws.send(json.dumps(request))
                continue

            # Handle connect response
            if frame.get("type") == "res" and frame.get("id") == req_id:
                if not frame.get("ok"):
                    error = frame.get("error", {})
                    raise ConnectionError(
                        f"Gateway connect failed: {error.get('message', 'unknown error')}"
                    )
                self._connected = True
                logger.info("Gateway connected (hello-ok)")
                return

        raise ConnectionError("Gateway connect: no response received")

    async def disconnect(self):
        """Close the Gateway WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None
            self._connected = False
            logger.info("Gateway disconnected")

    async def process_message(
        self,
        text: str,
        conversation: list[dict],
        attachments: list[dict] | None = None,
        model: str = "",
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        options = options or {}
        self._last_attachments = []
        await self._ensure_connected()

        session_key = options.get("session_key", "agent:main:main")
        req_id = str(uuid.uuid4())

        # Build chat.send RPC request
        params: dict = {
            "sessionKey": session_key,
            "message": text,
            "idempotencyKey": str(uuid.uuid4()),
            "timeoutMs": 120000,
        }
        if attachments:
            params["attachments"] = attachments

        request = {
            "type": "req",
            "id": req_id,
            "method": "chat.send",
            "params": params,
        }

        # Acquire lock for the entire streaming session
        async with self._ws_lock:
            await self._ws.send(json.dumps(request))

            # Receive streaming events with deadline-based timeout
            run_id: str | None = None
            seen_seqs: set[int] = set()
            prev_text = ""  # Gateway sends accumulated text; track for incremental delta
            deadline = asyncio.get_event_loop().time() + GATEWAY_STREAM_TIMEOUT
            async for raw in self._ws:
                if asyncio.get_event_loop().time() > deadline:
                    raise TimeoutError("Gateway stream timeout exceeded")

                frame = json.loads(raw)

                # chat.send RPC response (confirms runId, streaming starts)
                if frame.get("type") == "res" and frame.get("id") == req_id:
                    if not frame.get("ok"):
                        error = frame.get("error", {})
                        raise Exception(error.get("message", "Gateway RPC error"))
                    run_id = frame.get("payload", {}).get("runId")
                    logger.debug(f"Chat run started: runId={run_id}")
                    continue

                # Chat streaming events — filter by runId and deduplicate by seq
                if frame.get("type") == "event" and frame.get("event") == "chat":
                    payload = frame.get("payload", {})

                    # Ignore events until we know our runId, and filter other runs
                    if not run_id or payload.get("runId") != run_id:
                        continue

                    # Deduplicate (gateway sends broadcast + session copy)
                    seq = payload.get("seq")
                    if seq is not None:
                        if seq in seen_seqs:
                            continue
                        seen_seqs.add(seq)

                    state = payload.get("state")
                    logger.debug(f"Chat event: state={state}, runId={payload.get('runId')}, seq={seq}")

                    if state == "delta":
                        # Gateway delta contains accumulated text, convert to incremental
                        accumulated = _extract_text_from_message(payload.get("message"))
                        incremental = accumulated[len(prev_text):]
                        prev_text = accumulated
                        if incremental:
                            yield incremental

                    elif state in ("thinking", "tool_start", "tool_end", "tool"):
                        # Forward tool/thinking status to the app
                        if self._tool_status_callback:
                            tool_name = (payload.get("toolName")
                                         or payload.get("tool")
                                         or state)
                            tool_status = "running" if state in ("tool_start", "tool", "thinking") else "done"
                            await self._tool_status_callback(tool_name, tool_status)

                    elif state == "final":
                        # Final also contains accumulated text; yield any remaining
                        accumulated = _extract_text_from_message(payload.get("message"))
                        incremental = accumulated[len(prev_text):]
                        if incremental:
                            yield incremental
                        # Extract images from final message
                        self._last_attachments = _extract_images_from_message(payload.get("message"))
                        break

                    elif state in ("error", "aborted"):
                        error_msg = payload.get("errorMessage", "Agent error")
                        raise Exception(error_msg)

    async def get_history(self, session_key: str, limit: int = 100) -> list[dict]:
        """Query the Gateway for chat history via chat.history RPC."""
        await self._ensure_connected()

        async with self._ws_lock:
            req_id = str(uuid.uuid4())
            request = {
                "type": "req",
                "id": req_id,
                "method": "chat.history",
                "params": {"sessionKey": session_key, "limit": limit},
            }
            await self._ws.send(json.dumps(request))

            deadline = asyncio.get_event_loop().time() + GATEWAY_RPC_TIMEOUT
            async for raw in self._ws:
                if asyncio.get_event_loop().time() > deadline:
                    raise TimeoutError("chat.history timeout exceeded")

                frame = json.loads(raw)
                if frame.get("type") == "res" and frame.get("id") == req_id:
                    if frame.get("ok"):
                        return frame.get("payload", {}).get("messages", [])
                    error = frame.get("error", {})
                    raise Exception(error.get("message", "chat.history failed"))

        return []

    async def search_memory(self, session_key: str, query: str, limit: int = 20) -> list[dict]:
        """Search the Gateway for memories via memory.search RPC."""
        await self._ensure_connected()

        async with self._ws_lock:
            req_id = str(uuid.uuid4())
            request = {
                "type": "req",
                "id": req_id,
                "method": "memory.search",
                "params": {"sessionKey": session_key, "query": query, "limit": limit},
            }
            await self._ws.send(json.dumps(request))

            deadline = asyncio.get_event_loop().time() + GATEWAY_RPC_TIMEOUT
            async for raw in self._ws:
                if asyncio.get_event_loop().time() > deadline:
                    raise TimeoutError("memory.search timeout exceeded")

                frame = json.loads(raw)
                if frame.get("type") == "res" and frame.get("id") == req_id:
                    if frame.get("ok"):
                        return frame.get("payload", {}).get("memories", [])
                    error = frame.get("error", {})
                    raise Exception(error.get("message", "memory.search failed"))

        return []

    async def list_agents(self) -> list[dict]:
        """Query the Gateway for its list of agents via agents.list RPC."""
        await self._ensure_connected()

        async with self._ws_lock:
            req_id = str(uuid.uuid4())
            request = {
                "type": "req",
                "id": req_id,
                "method": "agents.list",
                "params": {},
            }
            await self._ws.send(json.dumps(request))

            deadline = asyncio.get_event_loop().time() + GATEWAY_RPC_TIMEOUT
            async for raw in self._ws:
                if asyncio.get_event_loop().time() > deadline:
                    raise TimeoutError("agents.list timeout exceeded")

                frame = json.loads(raw)
                if frame.get("type") == "res" and frame.get("id") == req_id:
                    if frame.get("ok"):
                        return frame.get("payload", {}).get("agents", [])
                    error = frame.get("error", {})
                    raise Exception(error.get("message", "agents.list failed"))

        return []
