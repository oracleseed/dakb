"""
DAKB Agent WebSocket Transport

Persistent bidirectional JSON-RPC 2.0 connection for agent communication.
Supports multi-agent multiplexing per token (one WS, multiple agent names).

Version: 1.0
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent WebSocket"])


# =============================================================================
# JSON-RPC 2.0 HELPERS
# =============================================================================

def parse_jsonrpc(raw: str) -> dict:
    """Parse and validate a JSON-RPC 2.0 message."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")

    if not isinstance(msg, dict):
        raise ValueError("JSON-RPC message must be an object")

    if msg.get("jsonrpc") != "2.0":
        raise ValueError("Missing or invalid 'jsonrpc' field (must be '2.0')")

    if "method" not in msg:
        raise ValueError("Missing 'method' field")

    return msg


def make_response(result: Any, req_id: str | None = None) -> str:
    """Create a JSON-RPC 2.0 success response."""
    resp: dict = {"jsonrpc": "2.0", "result": result}
    if req_id is not None:
        resp["id"] = req_id
    return json.dumps(resp)


def make_error(
    code: int, message: str, req_id: str | None = None, data: Any = None
) -> str:
    """Create a JSON-RPC 2.0 error response."""
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    resp: dict = {"jsonrpc": "2.0", "error": error}
    if req_id is not None:
        resp["id"] = req_id
    return json.dumps(resp)


def make_notification(method: str, params: dict) -> str:
    """Create a JSON-RPC 2.0 notification (no id = no reply expected)."""
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params})


# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
INSUFFICIENT_SCOPE = -32003
RATE_LIMITED = -32004
DUPLICATE_MESSAGE = -32005
AUTH_REQUIRED = -32010
AUTH_EXPIRED = -32011


# =============================================================================
# CONNECTION DATA
# =============================================================================

@dataclass
class AgentConnection:
    """Tracks a single token's WebSocket connection and its agent names."""
    websocket: Any
    token_id: str
    agent_names: list[str]
    connected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_heartbeat: float = field(default_factory=time.time)
    message_count: int = 0


# =============================================================================
# CONNECTION MANAGER
# =============================================================================

class AgentConnectionManager:
    """
    Manages agent WebSocket connections with multi-agent multiplexing.

    One token can own multiple agent names (local subagents).
    One WebSocket connection carries all agents for a token.
    """

    def __init__(self):
        # token_id -> AgentConnection
        self._connections: dict[str, AgentConnection] = {}
        # agent_name -> token_id (reverse lookup)
        self._agent_to_token: dict[str, str] = {}

    def register(
        self,
        websocket: Any,
        token_id: str,
        agent_names: list[str],
    ) -> None:
        """Register a token's WebSocket connection with its agent names."""
        # Check for name collisions with other tokens
        for name in agent_names:
            existing_token = self._agent_to_token.get(name)
            if existing_token and existing_token != token_id:
                raise ValueError(
                    f"Agent name '{name}' already owned by token "
                    f"'{existing_token}'"
                )

        # Unregister previous connection if re-connecting
        if token_id in self._connections:
            self.unregister(token_id)

        conn = AgentConnection(
            websocket=websocket,
            token_id=token_id,
            agent_names=agent_names,
        )
        self._connections[token_id] = conn

        for name in agent_names:
            self._agent_to_token[name] = token_id

        logger.info(
            "Registered token=%s with agents=%s (total connections: %d)",
            token_id, agent_names, len(self._connections),
        )

    def unregister(self, token_id: str) -> None:
        """Remove a token's connection and all its agent name mappings."""
        conn = self._connections.pop(token_id, None)
        if conn:
            for name in conn.agent_names:
                self._agent_to_token.pop(name, None)
            logger.info(
                "Unregistered token=%s agents=%s (total connections: %d)",
                token_id, conn.agent_names, len(self._connections),
            )

    def get_agent_connection(
        self, agent_name: str
    ) -> AgentConnection | None:
        """Resolve agent name -> token -> connection."""
        token_id = self._agent_to_token.get(agent_name)
        if token_id:
            return self._connections.get(token_id)
        return None

    def get_token_connection(self, token_id: str) -> AgentConnection | None:
        """Get the connection for a token ID."""
        return self._connections.get(token_id)

    def get_connected_agents(self) -> list[str]:
        """Get all currently connected agent names."""
        return list(self._agent_to_token.keys())

    def validate_from_agent(self, token_id: str, agent_name: str) -> bool:
        """Verify a token owns the given agent name (prevents impersonation)."""
        conn = self._connections.get(token_id)
        if not conn:
            return False
        return agent_name in conn.agent_names

    async def send_to_agent(self, agent_name: str, message: str) -> bool:
        """Send a message to an agent via its token's WebSocket."""
        conn = self.get_agent_connection(agent_name)
        if not conn:
            logger.warning("Agent '%s' not connected, cannot deliver", agent_name)
            return False
        try:
            if conn.websocket.client_state == WebSocketState.CONNECTED:
                await conn.websocket.send_text(message)
                return True
        except Exception as e:
            logger.error("Failed to send to agent '%s': %s", agent_name, e)
        return False

    def update_heartbeat(self, token_id: str) -> None:
        """Update the last heartbeat timestamp for a token."""
        conn = self._connections.get(token_id)
        if conn:
            conn.last_heartbeat = time.time()

    def get_stats(self) -> dict:
        """Get connection statistics."""
        return {
            "total_connections": len(self._connections),
            "total_agents": len(self._agent_to_token),
            "connections": {
                tid: {
                    "agents": c.agent_names,
                    "connected_at": c.connected_at.isoformat(),
                    "message_count": c.message_count,
                }
                for tid, c in self._connections.items()
            },
        }
