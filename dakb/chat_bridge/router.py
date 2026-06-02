"""Chat Webhook Router — FastAPI routes for ``/webhook/{platform}``.

Receives webhook payloads from chat platforms, validates signatures,
normalizes to the unified message schema, and publishes to the Redis
``stream:chat:inbound`` stream.

Routes:
  POST /webhook/{platform} -> validate -> normalize -> publish -> 200 OK
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)


def create_chat_router(
    adapter_registry,
    redis_client,
    session_manager,
) -> APIRouter:
    """Create a FastAPI router for chat webhook endpoints.

    Args:
        adapter_registry: AdapterRegistry with loaded platform adapters
        redis_client: RedisClient for publishing to stream:chat:inbound
        session_manager: ChatSessionManager for session CRUD

    Returns:
        FastAPI APIRouter with the ``/webhook/{platform}`` route.
    """
    router = APIRouter(tags=["chat-webhooks"])

    @router.post("/webhook/{platform}")
    async def handle_webhook(platform: str, request: Request):
        """Receive a webhook from a chat platform.

        Flow:
        1. Look up adapter for platform
        2. Validate webhook signature
        3. Normalize payload to unified schema via adapter.handle_webhook()
        4. Create or get the chat session
        5. Handle /invite and /uninvite commands
        6. Publish to stream:chat:inbound
        7. Return 200 OK
        """
        # 1. Look up adapter
        adapter = adapter_registry.get(platform)
        if adapter is None:
            raise HTTPException(
                status_code=404, detail=f"No adapter for platform: {platform}"
            )

        # 2. Validate signature
        body = await request.body()
        headers = dict(request.headers)
        if not adapter.validate_signature(headers, body):
            raise HTTPException(status_code=403, detail="Invalid webhook signature")

        # 3. Parse and normalize
        payload = json.loads(body) if body else {}
        normalized = await adapter.handle_webhook(payload, headers)

        if normalized is None:
            # Non-text update (callback_query, etc.) — acknowledge but don't process
            return {"status": "ok", "processed": False}

        params = normalized.get("params", {})
        composite_chat_id = params.get("composite_chat_id", "")
        external_user_id = params.get("external_user_id", "")
        external_chat_id = params.get("external_chat_id", "")

        # 4. Create or get session
        resolved_chat_id = external_chat_id or (
            composite_chat_id.split(":", 1)[-1]
            if ":" in composite_chat_id
            else composite_chat_id
        )
        await session_manager.get_or_create(
            platform=platform,
            external_chat_id=resolved_chat_id,
            external_user_id=external_user_id,
        )
        await session_manager.touch(composite_chat_id)

        # 5. Handle commands (/invite, /uninvite)
        # Adapter puts is_command, command, command_args at params level.
        if params.get("is_command"):
            command = params.get("command", "")
            args_raw = params.get("command_args", "")
            # Strip @ prefix from agent name
            agent_name = (
                args_raw.lstrip("@")
                if isinstance(args_raw, str)
                else (args_raw[0] if args_raw else "")
            )

            if command == "invite" and agent_name:
                await session_manager.invite_agent(composite_chat_id, agent_name)
                logger.info("Invited %s to %s", agent_name, composite_chat_id)
                return {"status": "ok", "command": "invite", "agent": agent_name}

            elif command == "uninvite" and agent_name:
                await session_manager.uninvite_agent(composite_chat_id, agent_name)
                logger.info("Uninvited %s from %s", agent_name, composite_chat_id)
                return {"status": "ok", "command": "uninvite", "agent": agent_name}

        # 6. Publish to Redis stream:chat:inbound
        stream_data = {"data": json.dumps(normalized)}
        await redis_client.stream_add("stream:chat:inbound", stream_data)

        logger.info("Chat webhook processed: %s -> %s", platform, composite_chat_id)
        return {"status": "ok", "processed": True}

    return router
