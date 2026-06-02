"""Bridge Queue Manager — Redis inbox, MongoDB offline queue, file fallback."""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Key patterns
INBOX_KEY = "bridge:inbox:{session_id}"
ARCHIVE_KEY = "bridge:archive:{session_id}"
LAST_SEEN_KEY = "bridge:last_seen:{session_id}"

# Safety limits
INBOX_CAP = 500
ARCHIVE_CAP = 100


class BridgeQueueManager:
    """Manages message delivery: Redis inbox (primary), MongoDB (offline), file (degraded)."""

    def __init__(self, redis, collection, fallback_dir: str = "/tmp"):
        self._redis = redis
        self._collection = collection  # dakb_bridge_queue MongoDB collection
        self._fallback_dir = Path(fallback_dir)

    async def enqueue(self, session_id: str, msg: dict, online: bool = True) -> None:
        """Enqueue a message for delivery to an agent session."""
        msg_json = json.dumps(msg, default=str)
        inbox_key = INBOX_KEY.format(session_id=session_id)

        if online:
            try:
                await self._redis.rpush(inbox_key, msg_json)
                await self._redis.ltrim(inbox_key, -INBOX_CAP, -1)
                # Notify watcher daemon that a new message is available
                await self._redis.rpush(f"bridge:watcher:notify:{session_id}", "1")
            except Exception:
                logger.warning(f"Redis unavailable for {session_id}, falling back to file")
                self._file_append(session_id, msg_json)
        else:
            # Offline: store in MongoDB for later delivery
            await self._collection.insert_one({
                "session_id": session_id,
                "msg_data": msg_json,
                "queued_at": msg.get("timestamp"),
            })

    async def get_pending(self, session_id: str) -> list[dict]:
        """Read and clear pending messages from Redis inbox. Dedup via last_seen."""
        inbox_key = INBOX_KEY.format(session_id=session_id)
        last_seen_key = LAST_SEEN_KEY.format(session_id=session_id)

        raw = await self._redis.lrange(inbox_key, 0, -1)
        if not raw:
            return []

        # Dedup: skip messages already processed
        last_seen = await self._redis.get(last_seen_key)
        last_seen_id = (last_seen if isinstance(last_seen, str) else last_seen.decode()) if last_seen else None

        messages = []
        past_last_seen = last_seen_id is None
        for r in raw:
            msg = json.loads(r)
            if not past_last_seen:
                if msg.get("msg_id") == last_seen_id:
                    past_last_seen = True
                continue
            messages.append(msg)

        # Clear inbox
        await self._redis.delete(inbox_key)

        # Update last_seen
        if messages:
            await self._redis.set(last_seen_key, messages[-1].get("msg_id", ""))

        return messages

    async def deliver_backlog(self, session_id: str) -> int:
        """Deliver queued MongoDB messages to Redis inbox (on reconnect)."""
        inbox_key = INBOX_KEY.format(session_id=session_id)
        cursor = self._collection.find({"session_id": session_id}).sort("queued_at", 1)
        docs = await cursor.to_list(length=1000)
        count = 0
        for doc in docs:
            await self._redis.rpush(inbox_key, doc["msg_data"])
            count += 1
        if count > 0:
            await self._collection.delete_many({"session_id": session_id})
            await self._redis.ltrim(inbox_key, -INBOX_CAP, -1)
        return count

    async def enforce_cap(self, session_id: str, max_size: int = INBOX_CAP) -> None:
        """Enforce inbox size cap via LTRIM."""
        inbox_key = INBOX_KEY.format(session_id=session_id)
        await self._redis.ltrim(inbox_key, -max_size, -1)

    async def get_inbox_depth(self, session_id: str) -> int:
        """Return number of pending messages in Redis inbox."""
        inbox_key = INBOX_KEY.format(session_id=session_id)
        return await self._redis.llen(inbox_key)

    async def get_queue_depth(self, session_id: str) -> int:
        """Return number of messages in MongoDB offline queue."""
        return await self._collection.count_documents({"session_id": session_id})

    def _file_append(self, session_id: str, msg_json: str) -> None:
        """Degraded fallback: append to file when Redis is down."""
        fallback = self._fallback_dir / f"bridge_inbox_{session_id}.jsonl"
        with fallback.open("a") as f:
            f.write(msg_json + "\n")
