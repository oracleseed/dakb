"""
DAKB Presence System

Redis-backed agent presence tracking with heartbeat protocol,
capability-based routing, and status transitions.

Version: 1.0
"""

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Heartbeat interval and TTL
HEARTBEAT_TTL_SECONDS = 30
IDLE_TIMEOUT_SECONDS = 300  # 5 minutes
SNAPSHOT_INTERVAL_SECONDS = 60


class PresenceManager:
    """
    Manages agent presence state via Redis.

    - Agents send heartbeats every 30s (renews TTL)
    - Redis key expires after 30s if no heartbeat -> offline
    - Status transitions: online -> idle (5min) -> offline (key expired)
    - Capability metadata enables task routing
    """

    def __init__(self, redis_client, mongo_collection=None):
        self._redis = redis_client
        self._mongo = mongo_collection  # For periodic snapshots
        self._last_activity: dict[str, float] = {}  # agent_name -> timestamp

    async def set_status(
        self,
        agent_name: str,
        token_id: str,
        status: str = "online",
        capabilities: list[str] | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Set agent presence data in Redis."""
        now = datetime.now(timezone.utc)
        data = {
            "agent_name": agent_name,
            "token_id": token_id,
            "status": status,
            "capabilities": capabilities or [],
            "metadata": metadata or {},
            "updated_at": now.isoformat(),
        }
        await self._redis.set_presence(
            agent_name, data, ttl_seconds=HEARTBEAT_TTL_SECONDS
        )
        self._last_activity[agent_name] = time.time()
        logger.debug("Presence set: %s -> %s", agent_name, status)

    async def heartbeat(
        self,
        agent_name: str,
        status: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Renew agent presence TTL. Optionally update status/metadata."""
        existing = await self._redis.get_presence(agent_name)
        if not existing:
            logger.warning("Heartbeat for unknown agent: %s", agent_name)
            return

        if status:
            existing["status"] = status
        if metadata:
            existing["metadata"] = {**existing.get("metadata", {}), **metadata}
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()

        await self._redis.set_presence(
            agent_name, existing, ttl_seconds=HEARTBEAT_TTL_SECONDS
        )
        self._last_activity[agent_name] = time.time()

    async def set_offline(self, agent_name: str) -> None:
        """Explicitly set an agent offline (remove presence key)."""
        await self._redis.delete_presence(agent_name)
        self._last_activity.pop(agent_name, None)
        logger.info("Agent offline: %s", agent_name)

    async def query(
        self,
        filter_status: list[str] | None = None,
        filter_capabilities: list[str] | None = None,
    ) -> dict:
        """
        Query online agents with optional filters.

        Args:
            filter_status: Only return agents with these statuses
            filter_capabilities: Only return agents with ALL of these capabilities
        """
        all_presence = await self._redis.get_all_presence()
        result = {}

        for agent_name, data in all_presence.items():
            # Filter by status
            if filter_status and data.get("status") not in filter_status:
                continue

            # Filter by capabilities (ALL must match)
            if filter_capabilities:
                agent_caps = set(data.get("capabilities", []))
                if not set(filter_capabilities).issubset(agent_caps):
                    continue

            result[agent_name] = data

        return result

    async def get_status(self, agent_name: str) -> dict | None:
        """Get a single agent's presence data."""
        return await self._redis.get_presence(agent_name)

    async def check_idle_transitions(self) -> list[str]:
        """
        Check for agents that should transition to idle.

        Called periodically. Agents with no activity for IDLE_TIMEOUT_SECONDS
        are transitioned from 'online' to 'idle'.
        """
        now = time.time()
        transitioned = []

        for agent_name, last_active in list(self._last_activity.items()):
            if now - last_active > IDLE_TIMEOUT_SECONDS:
                presence = await self._redis.get_presence(agent_name)
                if presence and presence.get("status") == "online":
                    presence["status"] = "idle"
                    await self._redis.set_presence(
                        agent_name, presence, ttl_seconds=HEARTBEAT_TTL_SECONDS
                    )
                    transitioned.append(agent_name)
                    logger.info("Auto-idle: %s", agent_name)

        return transitioned

    async def snapshot_to_mongo(self) -> None:
        """Persist current presence state to MongoDB for durability."""
        if not self._mongo:
            return
        all_presence = await self._redis.get_all_presence()
        if all_presence:
            snapshot = {
                "snapshot_at": datetime.now(timezone.utc),
                "agents": all_presence,
            }
            self._mongo.insert_one(snapshot)
            logger.debug("Presence snapshot: %d agents", len(all_presence))
