"""
DAKB Redis Client

Async Redis client with connection pooling, Streams helpers,
and message deduplication support.

Powers the real-time stack: presence keys, agent message streams,
task leases, and (later) the chat bridge.

The whole real-time stack is OPTIONAL. If Redis is unavailable the
gateway must keep running with real-time features disabled — this
module is written so that import never fails and ``connect()`` failures
are surfaced as a clean boolean rather than crashing startup.

Configuration:
    redis_url defaults to env ``DAKB_REDIS_URL`` or ``redis://localhost:6379/0``.

Version: 1.0
"""

import json
import logging
import os
from datetime import datetime, timezone

try:  # pragma: no cover - import guard
    import redis.asyncio as aioredis
    from redis.exceptions import ResponseError
    REDIS_AVAILABLE = True
except Exception:  # pragma: no cover - redis not installed
    aioredis = None  # type: ignore[assignment]
    ResponseError = Exception  # type: ignore[assignment,misc]
    REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Dedup cache TTL in seconds (10 minutes)
DEDUP_TTL_SECONDS = 600

# Default stream maxlen (prevent unbounded growth)
DEFAULT_STREAM_MAXLEN = 10000

# Default Redis URL — overridable via env. No hardcoded hosts.
DEFAULT_REDIS_URL = os.getenv("DAKB_REDIS_URL", "redis://localhost:6379/0")


class RedisClient:
    """
    Async Redis client for DAKB real-time communication.

    Wraps redis.asyncio with convenience methods for:
    - Streams (XADD, XREADGROUP, XACK, consumer groups)
    - Message deduplication (SET NX with TTL)
    - Presence keys (SET with TTL)
    - Task leases (SET with TTL, for zombie prevention)
    - Health checks

    Graceful degradation: ``connect()`` returns ``False`` (instead of
    raising) when Redis is unreachable or the ``redis`` package is not
    installed, so callers can disable real-time features without crashing.
    """

    def __init__(self, redis_url: str | None = None):
        self._redis_url = redis_url or DEFAULT_REDIS_URL
        self._redis = None

    async def connect(self) -> bool:
        """
        Create Redis connection pool and verify connectivity.

        Returns True on success, False if Redis is unavailable. Never raises
        on connection failure so the gateway can start without Redis.
        """
        if not REDIS_AVAILABLE:
            logger.warning(
                "redis package not installed — real-time features disabled"
            )
            return False
        try:
            self._redis = aioredis.from_url(
                self._redis_url,
                decode_responses=True,
                max_connections=20,
            )
            await self._redis.ping()
            logger.info("Redis connected: %s", self._redis_url)
            return True
        except Exception as exc:
            logger.warning(
                "Redis unavailable at %s (%s) — real-time features disabled",
                self._redis_url,
                exc,
            )
            self._redis = None
            return False

    async def disconnect(self) -> None:
        """Close Redis connection pool."""
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception as exc:  # pragma: no cover - best-effort close
                logger.debug("Error closing Redis connection: %s", exc)
            self._redis = None
            logger.info("Redis disconnected")

    async def health_check(self) -> bool:
        """Check if Redis is connected and responsive."""
        if not self._redis:
            return False
        try:
            return await self._redis.ping()
        except Exception:
            return False

    @property
    def is_connected(self) -> bool:
        """Whether a live Redis connection is available."""
        return self._redis is not None

    @property
    def redis(self):
        """Get the underlying Redis client. Raises if not connected."""
        if not self._redis:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._redis

    # =========================================================================
    # STREAMS
    # =========================================================================

    async def stream_add(
        self,
        stream: str,
        fields: dict,
        maxlen: int = DEFAULT_STREAM_MAXLEN,
    ) -> str:
        """Add a message to a Redis Stream (XADD)."""
        msg_id = await self.redis.xadd(
            stream, fields, maxlen=maxlen, approximate=True
        )
        return msg_id

    async def stream_read_group(
        self,
        group: str,
        consumer: str,
        streams: dict,
        count: int = 10,
        block: int = 0,
    ) -> list:
        """Read messages from a consumer group (XREADGROUP)."""
        result = await self.redis.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams=streams,
            count=count,
            block=block,
        )
        return result or []

    async def stream_ack(self, stream: str, group: str, message_id: str) -> int:
        """Acknowledge a message in a consumer group (XACK)."""
        return await self.redis.xack(stream, group, message_id)

    async def ensure_consumer_group(
        self,
        stream: str,
        group: str,
        start_id: str = "0",
    ) -> None:
        """Create a consumer group if it doesn't exist."""
        try:
            await self.redis.xgroup_create(
                name=stream, groupname=group, id=start_id, mkstream=True
            )
            logger.info("Created consumer group %s on %s", group, stream)
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                pass  # Group already exists
            else:
                raise

    async def stream_pending(self, stream: str, group: str) -> list:
        """Get pending messages for a consumer group (XPENDING)."""
        return await self.redis.xpending(stream, group)

    # =========================================================================
    # DEDUPLICATION
    # =========================================================================

    async def is_duplicate(self, message_id: str) -> bool:
        """
        Check if a message_id has been seen before.

        Uses SET NX (set if not exists) with TTL.
        Returns True if duplicate (key already existed).
        Returns False if new (key was set).
        """
        result = await self.redis.set(
            f"dedup:{message_id}", "1", nx=True, ex=DEDUP_TTL_SECONDS
        )
        return result is None  # None means key already existed

    # =========================================================================
    # PRESENCE KEYS
    # =========================================================================

    async def set_presence(
        self, agent_id: str, data: dict, ttl_seconds: int = 30
    ) -> None:
        """Set agent presence data with TTL."""
        await self.redis.set(
            f"presence:{agent_id}",
            json.dumps(data),
            ex=ttl_seconds,
        )

    async def get_presence(self, agent_id: str) -> dict | None:
        """Get agent presence data."""
        data = await self.redis.get(f"presence:{agent_id}")
        if data:
            return json.loads(data)
        return None

    async def get_all_presence(self, pattern: str = "presence:*") -> dict:
        """Get all agent presence data matching pattern."""
        result = {}
        async for key in self.redis.scan_iter(match=pattern):
            data = await self.redis.get(key)
            if data:
                agent_id = key.replace("presence:", "", 1)
                result[agent_id] = json.loads(data)
        return result

    async def delete_presence(self, agent_id: str) -> None:
        """Remove agent presence key."""
        await self.redis.delete(f"presence:{agent_id}")

    # =========================================================================
    # TASK LEASES (Zombie Prevention)
    # =========================================================================

    async def set_lease(
        self, task_id: str, assigned_to: str, ttl_seconds: int = 60
    ) -> None:
        """Create a task lease with assigned agent and TTL."""
        data = json.dumps({
            "assigned_to": assigned_to,
            "renewed_at": datetime.now(timezone.utc).isoformat(),
        })
        await self.redis.set(f"task:lease:{task_id}", data, ex=ttl_seconds)

    async def renew_lease(self, task_id: str, ttl_seconds: int = 60) -> bool:
        """Refresh TTL on an existing lease. Returns False if lease expired."""
        data = await self.redis.get(f"task:lease:{task_id}")
        if not data:
            return False
        parsed = json.loads(data)
        parsed["renewed_at"] = datetime.now(timezone.utc).isoformat()
        await self.redis.set(
            f"task:lease:{task_id}", json.dumps(parsed), ex=ttl_seconds
        )
        return True

    async def get_lease(self, task_id: str) -> dict | None:
        """Get lease data for a task, or None if no lease exists."""
        data = await self.redis.get(f"task:lease:{task_id}")
        if data:
            return json.loads(data)
        return None

    async def delete_lease(self, task_id: str) -> None:
        """Remove a task lease key."""
        await self.redis.delete(f"task:lease:{task_id}")

    async def get_active_leases(self) -> dict:
        """Scan all task:lease:* keys and return {task_id: lease_data}."""
        result = {}
        async for key in self.redis.scan_iter(match="task:lease:*"):
            data = await self.redis.get(key)
            if data:
                task_id = key.replace("task:lease:", "", 1)
                result[task_id] = json.loads(data)
        return result

    # =========================================================================
    # GENERIC KEY OPERATIONS
    # =========================================================================

    async def set_key(
        self, key: str, value: str, ttl_seconds: int | None = None
    ) -> None:
        """Set a Redis key with optional TTL."""
        if ttl_seconds:
            await self.redis.set(key, value, ex=ttl_seconds)
        else:
            await self.redis.set(key, value)

    async def get_key(self, key: str) -> str | None:
        """Get a Redis key value."""
        return await self.redis.get(key)

    async def delete_key(self, key: str) -> None:
        """Delete a Redis key."""
        await self.redis.delete(key)
