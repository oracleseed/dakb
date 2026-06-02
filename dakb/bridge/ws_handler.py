"""Bridge WebSocket handler — manages bridge client connections."""
import logging
import time

from fastapi import WebSocket

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "bridge:heartbeat:{session_id}"
HEARTBEAT_TTL = 60  # seconds


class BridgeConnectionManager:
    """Manages WebSocket connections from bridge clients."""

    def __init__(self, redis, queue):
        self._redis = redis
        self._queue = queue
        self._connections: dict[str, WebSocket] = {}

    def register(self, session_id: str, ws: WebSocket) -> None:
        """Register a bridge client WebSocket connection."""
        self._connections[session_id] = ws
        logger.info(f"Bridge registered: {session_id}")

    def unregister(self, session_id: str) -> None:
        """Unregister a bridge client connection."""
        self._connections.pop(session_id, None)
        logger.info(f"Bridge unregistered: {session_id}")

    def is_connected(self, session_id: str) -> bool:
        """Check if a session has an active bridge connection."""
        return session_id in self._connections

    async def on_connect(self, session_id: str, ws: WebSocket) -> int:
        """Handle new bridge connection: register + deliver backlog."""
        self.register(session_id, ws)
        await self.update_heartbeat(session_id)
        count = await self._queue.deliver_backlog(session_id)
        if count > 0:
            logger.info(f"Delivered {count} backlog messages to {session_id}")
        return count

    async def push(self, session_id: str, msg: dict) -> bool:
        """Push a message to connected bridge client, or queue if offline."""
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_json(msg)
                return True
            except Exception:
                logger.warning(f"Failed to push to {session_id}, queuing offline")
                self.unregister(session_id)

        # Offline: queue in MongoDB
        await self._queue.enqueue(session_id, msg, online=False)
        return False

    async def update_heartbeat(self, session_id: str) -> None:
        """Update heartbeat timestamp in Redis."""
        key = HEARTBEAT_KEY.format(session_id=session_id)
        await self._redis.set(key, str(time.time()), ex=HEARTBEAT_TTL)

    async def cleanup(self, session_id: str) -> None:
        """Clean up on disconnect: remove connection + heartbeat."""
        self.unregister(session_id)
        key = HEARTBEAT_KEY.format(session_id=session_id)
        await self._redis.delete(key)
        logger.info(f"Bridge cleanup complete: {session_id}")
