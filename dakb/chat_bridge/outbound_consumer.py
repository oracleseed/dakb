"""Chat Outbound Consumer — reads ``stream:chat:outbound`` and delivers via the platform adapter.

Flow:
  Agent sends chat.send via WebSocket -> Message Router
    -> Redis stream:chat:outbound -> OutboundConsumer reads
    -> AdapterRegistry selects platform adapter
    -> adapter.send_message(chat_id, content)
    -> User sees response in chat platform
"""

import json
import logging

logger = logging.getLogger(__name__)

STREAM_KEY = "stream:chat:outbound"
CONSUMER_GROUP = "chatbridge:outbound:cg"


class OutboundConsumer:
    """Consume messages from ``stream:chat:outbound`` and deliver them to
    external chat platforms via the appropriate adapter.
    """

    def __init__(self, redis_client, adapter_registry, session_manager):
        self._redis = redis_client
        self._registry = adapter_registry
        self._sessions = session_manager

    async def deliver(self, params: dict) -> dict:
        """Deliver a single outbound message to the appropriate chat platform.

        Args:
            params: Message params with composite_chat_id, content, from_agent.

        Returns:
            dict with delivery result or error.
        """
        composite_chat_id = params.get("composite_chat_id", "")
        content = params.get("content", "")

        # 1. Look up session to get platform + external chat ID
        session = await self._sessions.get_session(composite_chat_id)
        if not session:
            logger.warning("No session found for %s", composite_chat_id)
            return {"error": f"Session not found: {composite_chat_id}"}

        platform = session.get("platform")
        external_chat_id = session.get("external_chat_id")

        # 2. Get adapter for platform
        adapter = self._registry.get(platform)
        if not adapter:
            logger.warning("No adapter for platform: %s", platform)
            return {"error": f"No adapter for platform: {platform}"}

        # 3. Send via adapter
        try:
            result = await adapter.send_message(external_chat_id, content)
            logger.info(
                "Outbound delivered: %s -> %s",
                params.get("from_agent", "?"),
                composite_chat_id,
            )
            return result
        except Exception as e:
            logger.error("Outbound delivery failed: %s", e)
            return {"error": str(e)}

    async def process_one(self, stream_id: str, fields: dict) -> dict:
        """Process a single stream entry from ``stream:chat:outbound``.

        Args:
            stream_id: Redis stream entry ID (e.g., "1500-0").
            fields: Entry fields dict with a "data" key containing JSON.

        Returns:
            dict with result or error.
        """
        raw_data = fields.get("data", "")
        try:
            parsed = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Malformed outbound stream entry %s: %s", stream_id, e)
            return {"error": f"Malformed data: {e}"}

        params = parsed.get("params", parsed)
        return await self.deliver(params)

    async def run_once(self, count: int = 10) -> list[dict]:
        """Read and process a batch of outbound messages.

        Returns:
            List of delivery results.
        """
        await self._redis.ensure_consumer_group(STREAM_KEY, CONSUMER_GROUP)

        entries = await self._redis.stream_read_group(
            group=CONSUMER_GROUP,
            consumer="outbound-worker",
            streams={STREAM_KEY: ">"},
            count=count,
        )

        results = []
        for _stream_name, messages in entries:
            for msg_id, fields in messages:
                result = await self.process_one(msg_id, fields)
                results.append(result)
                # ACK after successful processing
                if "error" not in result:
                    await self._redis.stream_ack(STREAM_KEY, CONSUMER_GROUP, msg_id)

        return results
