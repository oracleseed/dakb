"""
DAKB Message Router

Unified routing layer for agent-to-agent messages.
Handles direct routing, deduplication, scope enforcement,
and delivery acknowledgment.

Phase 1 implements default-internal routing. Task delegation and the
optional chat bridge are wired in via injected collaborators that may be
``None`` when those subsystems are not enabled.

Version: 1.0
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MessageRouter:
    """
    Routes messages between agents via WebSocket + Redis Streams.

    Default behavior routes messages internally between connected agents.
    Task delegation (``task_router``) and the chat bridge (``session_manager``,
    ``outbound_consumer``, ``alert_config_collection``) are optional — the
    router degrades gracefully when they are not provided.
    """

    def __init__(
        self,
        redis_client,
        conn_manager,
        presence_manager,
        task_router=None,
        # Optional chat bridge collaborators (system works without them)
        session_manager=None,
        outbound_consumer=None,
        alert_config_collection=None,
    ):
        self._redis = redis_client
        self._conn = conn_manager
        self._presence = presence_manager
        self._task_router = task_router
        # Optional chat bridge
        self._sessions = session_manager
        self._outbound = outbound_consumer
        self._alert_config = alert_config_collection

    async def route_message(
        self,
        method: str,
        params: dict,
        token_id: str,
    ) -> dict:
        """
        Route an agent message through validation, dedup, and delivery.

        Args:
            method: JSON-RPC method (message.send, presence.query, etc.)
            params: Method parameters
            token_id: Authenticated token ID

        Returns:
            dict with delivery status

        Raises:
            ValueError: Missing required fields
            PermissionError: from_agent impersonation attempt
        """
        if method == "message.send":
            return await self._handle_message_send(params, token_id)
        elif method == "presence.query":
            return await self._handle_presence_query(params)
        elif method == "presence.heartbeat":
            return await self._handle_heartbeat(params, token_id)
        elif method == "stream.replay":
            return await self._handle_stream_replay(params, token_id)
        elif method == "stream.ack":
            return await self._handle_stream_ack(params, token_id)
        elif method == "task.request":
            return await self._handle_task_request(params, token_id)
        elif method == "task.response":
            return await self._handle_task_response(params, token_id)
        elif method == "task.heartbeat":
            return await self._handle_task_heartbeat(params, token_id)
        elif method == "task.claim":
            return await self._handle_task_claim(params, token_id)
        # Chat bridge methods (optional)
        elif method == "chat.inbound":
            return await self._handle_chat_inbound(params, token_id)
        elif method == "chat.send":
            return await self._handle_chat_send(params, token_id)
        elif method == "chat.invite":
            return await self._handle_chat_invite(params, token_id)
        elif method == "chat.uninvite":
            return await self._handle_chat_uninvite(params, token_id)
        else:
            raise ValueError(f"Unknown method: {method}")

    async def _handle_message_send(self, params: dict, token_id: str) -> dict:
        """Handle message.send -- validate, dedup, deliver."""
        # 1. Validate required fields
        message_id = params.get("message_id")
        if not message_id:
            raise ValueError("message_id is required for message.send")

        from_agent = params.get("from_agent")
        if not from_agent:
            raise ValueError("from_agent is required for message.send")

        recipient = params.get("recipient")
        if not recipient:
            raise ValueError("recipient is required for message.send")

        # 2. Validate from_agent ownership (prevents impersonation)
        if not self._conn.validate_from_agent(token_id, from_agent):
            raise PermissionError(
                f"Token '{token_id}' cannot send as '{from_agent}' "
                "-- impersonation rejected"
            )

        # 3. Emergency escalation: CRITICAL priority bypasses normal routing
        priority = params.get("priority", "normal")
        if priority == "critical" and self._alert_config:
            return await self._handle_escalation(params, token_id)

        # 4. Check deduplication
        is_dup = await self._redis.is_duplicate(message_id)
        if is_dup:
            logger.info("Duplicate message dropped: %s", message_id)
            return {
                "delivered": False,
                "duplicate": True,
                "message_id": message_id,
            }

        # 5. Route to recipient via Redis Stream (default internal)
        stream_key = f"stream:agent:{recipient}"
        stream_data = {
            "data": json.dumps({
                "method": "message.received",
                "params": {
                    "message_id": message_id,
                    "sender": from_agent,
                    "recipient": recipient,
                    "subject": params.get("subject", ""),
                    "content": params.get("content", ""),
                    "priority": params.get("priority", "normal"),
                },
            })
        }
        await self._redis.stream_add(stream_key, stream_data)

        # 6. Try direct WebSocket delivery (real-time)
        from dakb.gateway.agent_websocket import make_notification
        notification = make_notification("message.received", {
            "message_id": message_id,
            "sender": from_agent,
            "subject": params.get("subject", ""),
            "content": params.get("content", ""),
            "priority": params.get("priority", "normal"),
        })
        delivered_ws = await self._conn.send_to_agent(recipient, notification)

        # 7. Send delivery ACK to sender
        ack_notification = make_notification("message.ack", {
            "message_id": message_id,
            "delivered_to": recipient,
            "delivered_at": datetime.now(timezone.utc).isoformat(),
            "delivered_ws": delivered_ws,
        })
        await self._conn.send_to_agent(from_agent, ack_notification)

        logger.info(
            "Message routed: %s -> %s (id=%s, ws=%s)",
            from_agent, recipient, message_id, delivered_ws,
        )

        return {
            "delivered": True,
            "duplicate": False,
            "message_id": message_id,
            "delivered_ws": delivered_ws,
        }

    async def _handle_presence_query(self, params: dict) -> dict:
        """Handle presence.query -- return filtered agent presence."""
        result = await self._presence.query(
            filter_status=params.get("filter_status"),
            filter_capabilities=params.get("filter_capabilities"),
        )
        return {"agents": result}

    async def _handle_heartbeat(self, params: dict, token_id: str) -> dict:
        """Handle presence.heartbeat -- renew presence TTL."""
        agent_name = params.get("agent_name")
        if not agent_name:
            # Use the first agent name registered for this token
            conn = self._conn.get_token_connection(token_id)
            if conn and conn.agent_names:
                agent_name = conn.agent_names[0]

        if agent_name:
            await self._presence.heartbeat(
                agent_name,
                status=params.get("status"),
                metadata=params.get("metadata"),
            )
        return {"status": "ok"}

    async def replay_stream(
        self, agent_name: str, last_id: str = "0", count: int = 100
    ) -> list:
        """
        Replay unacknowledged messages from an agent's stream.

        Used on reconnect: agent provides its last acknowledged message ID,
        server returns all messages after that point.
        """
        stream_key = f"stream:agent:{agent_name}"
        group = f"agent:{agent_name}:cg"

        # Ensure consumer group exists
        await self._redis.ensure_consumer_group(stream_key, group)

        # Read pending messages from last_id
        result = await self._redis.stream_read_group(
            group=group,
            consumer=agent_name,
            streams={stream_key: last_id},
            count=count,
        )

        messages = []
        for _stream_name, entries in result:
            for msg_id, fields in entries:
                data = fields.get("data")
                if data:
                    try:
                        parsed = (
                            json.loads(data) if isinstance(data, str) else data
                        )
                        messages.append({"stream_id": msg_id, **parsed})
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("Unparseable stream message: %s", msg_id)

        logger.info(
            "Replay for %s: %d messages from %s",
            agent_name, len(messages), last_id,
        )
        return messages

    async def ack_message(self, agent_name: str, message_id: str) -> int:
        """Acknowledge a message in an agent's consumer group."""
        stream_key = f"stream:agent:{agent_name}"
        group = f"agent:{agent_name}:cg"
        return await self._redis.stream_ack(stream_key, group, message_id)

    async def _handle_stream_replay(self, params: dict, token_id: str) -> dict:
        """Handle stream.replay -- return unacknowledged messages."""
        agent_name = params.get("agent_name")
        last_id = params.get("last_id", "0")
        messages = await self.replay_stream(agent_name, last_id=last_id)
        return {"messages": messages, "count": len(messages)}

    async def _handle_stream_ack(self, params: dict, token_id: str) -> dict:
        """Handle stream.ack -- acknowledge a message."""
        agent_name = params.get("agent_name")
        message_id = params.get("stream_id")
        result = await self.ack_message(agent_name, message_id)
        return {"acknowledged": result}

    # =========================================================================
    # TASK DELEGATION HANDLERS
    # =========================================================================

    async def _handle_task_request(self, params: dict, token_id: str) -> dict:
        """Handle task.request -- create and optionally auto-assign a task."""
        if not self._task_router:
            raise ValueError("Task delegation not available")

        from_agent = params.get("from_agent")
        if not from_agent:
            raise ValueError("from_agent is required for task.request")
        if not self._conn.validate_from_agent(token_id, from_agent):
            raise PermissionError(
                f"Token '{token_id}' cannot send as '{from_agent}'"
            )

        from dakb.db.schemas import DakbTaskCreate
        create = DakbTaskCreate(
            task_type=params.get("task_type", "generic"),
            requester_id=from_agent,
            recipient_id=params.get("recipient"),
            required_capabilities=params.get("required_capabilities"),
            prefer_status=params.get("prefer_status"),
            payload=params.get("payload", {}),
            timeout_seconds=params.get("timeout_seconds", 300),
            lease_ttl_seconds=params.get("lease_ttl_seconds", 60),
        )

        task = await self._task_router.create_task(create)

        # Auto-assign if recipient specified
        recipient = params.get("recipient")
        if not recipient and params.get("required_capabilities"):
            recipient = await self._task_router.find_best_agent(
                params["required_capabilities"],
                prefer_status=params.get("prefer_status"),
            )

        if recipient:
            # Notify assignee
            from dakb.gateway.agent_websocket import make_notification
            notification = make_notification("task.assigned", {
                "task_id": task.task_id,
                "task_type": task.task_type,
                "payload": task.payload,
                "timeout_seconds": task.timeout_seconds,
                "requester_id": from_agent,
            })
            await self._conn.send_to_agent(recipient, notification)

        logger.info(
            "Task request: %s from %s -> %s",
            task.task_id, from_agent, recipient,
        )
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "assigned_to": recipient,
        }

    async def _handle_task_response(self, params: dict, token_id: str) -> dict:
        """Handle task.response -- complete or fail a task."""
        if not self._task_router:
            raise ValueError("Task delegation not available")

        from_agent = params.get("from_agent")
        if from_agent and not self._conn.validate_from_agent(
            token_id, from_agent
        ):
            raise PermissionError(
                f"Token '{token_id}' cannot send as '{from_agent}'"
            )

        task_id = params.get("task_id")
        status = params.get("status")

        if status == "completed":
            task = await self._task_router.complete_task(
                task_id, params.get("result", {})
            )
        elif status == "failed":
            task = await self._task_router.fail_task(
                task_id, params.get("error", "Unknown error")
            )
        else:
            raise ValueError(f"Invalid task response status: {status}")

        # Notify requester
        from dakb.gateway.agent_websocket import make_notification
        notification = make_notification(f"task.{status}", {
            "task_id": task_id,
            "result": task.result,
            "error": task.error,
        })
        await self._conn.send_to_agent(task.requester_id, notification)

        return {"task_id": task_id, "status": task.status.value}

    async def _handle_task_heartbeat(self, params: dict, token_id: str) -> dict:
        """Handle task.heartbeat -- renew task lease."""
        if not self._task_router:
            raise ValueError("Task delegation not available")

        from_agent = params.get("from_agent")
        if from_agent and not self._conn.validate_from_agent(
            token_id, from_agent
        ):
            raise PermissionError(
                f"Token '{token_id}' cannot send as '{from_agent}'"
            )

        task_id = params.get("task_id")
        renewed = await self._task_router.heartbeat(task_id)

        return {"task_id": task_id, "renewed": renewed}

    async def _handle_task_claim(self, params: dict, token_id: str) -> dict:
        """Handle task.claim -- agent claims a pending task."""
        if not self._task_router:
            raise ValueError("Task delegation not available")

        from_agent = params.get("from_agent")
        if not from_agent:
            raise ValueError("from_agent is required for task.claim")
        if not self._conn.validate_from_agent(token_id, from_agent):
            raise PermissionError(
                f"Token '{token_id}' cannot send as '{from_agent}'"
            )

        task_id = params.get("task_id")
        task = await self._task_router.claim_task(task_id, from_agent)

        return {
            "task_id": task_id,
            "status": task.status.value,
            "assigned_to": from_agent,
        }

    # =========================================================================
    # CHAT BRIDGE HANDLERS (optional)
    # =========================================================================

    async def _handle_chat_inbound(self, params: dict, token_id: str) -> dict:
        """Handle chat.inbound -- deliver external message to invited agents."""
        if not self._sessions:
            raise ValueError("Chat bridge not available")

        composite_chat_id = params.get("composite_chat_id", "")
        content = params.get("content", "")

        session = await self._sessions.get_session(composite_chat_id)
        if not session:
            return {"delivered_to": [], "error": "Session not found"}

        invited = session.get("invited_agents", [])
        delivered_to = []

        from dakb.gateway.agent_websocket import make_notification
        for agent_name in invited:
            notification = make_notification("chat.inbound", {
                "composite_chat_id": composite_chat_id,
                "content": content,
                "platform": params.get("platform", ""),
                "external_user_id": params.get("external_user_id", ""),
            })
            ws_ok = await self._conn.send_to_agent(agent_name, notification)
            if ws_ok:
                delivered_to.append(agent_name)

        return {"delivered_to": delivered_to}

    async def _handle_chat_send(self, params: dict, token_id: str) -> dict:
        """Handle chat.send -- agent sends message to external chat."""
        from_agent = params.get("from_agent")
        if from_agent and not self._conn.validate_from_agent(
            token_id, from_agent
        ):
            raise PermissionError(
                f"Token '{token_id}' cannot send as '{from_agent}' "
                "-- impersonation rejected"
            )

        # Publish to stream:chat:outbound for async delivery
        stream_data = {"data": json.dumps({
            "method": "chat.send",
            "params": params,
        })}
        await self._redis.stream_add("stream:chat:outbound", stream_data)

        return {
            "published": True,
            "composite_chat_id": params.get("composite_chat_id"),
        }

    async def _handle_chat_invite(self, params: dict, token_id: str) -> dict:
        """Handle chat.invite -- add agent to chat session."""
        if not self._sessions:
            raise ValueError("Chat bridge not available")

        composite_chat_id = params.get("composite_chat_id")
        agent_name = params.get("agent_name")
        result = await self._sessions.invite_agent(composite_chat_id, agent_name)
        return {
            "invited": result,
            "agent": agent_name,
            "chat": composite_chat_id,
        }

    async def _handle_chat_uninvite(self, params: dict, token_id: str) -> dict:
        """Handle chat.uninvite -- remove agent from chat session."""
        if not self._sessions:
            raise ValueError("Chat bridge not available")

        composite_chat_id = params.get("composite_chat_id")
        agent_name = params.get("agent_name")
        result = await self._sessions.uninvite_agent(
            composite_chat_id, agent_name
        )
        return {
            "uninvited": result,
            "agent": agent_name,
            "chat": composite_chat_id,
        }

    async def _handle_escalation(self, params: dict, token_id: str) -> dict:
        """Handle emergency escalation for CRITICAL priority messages."""
        recipient = params.get("recipient", "")
        alert_doc = await self._alert_config.find_one({"user_id": recipient})

        if not alert_doc:
            # No alert config -- fall through to normal internal routing
            logger.warning(
                "No alert config for %s, routing internally", recipient
            )
            # Skip escalation check to avoid infinite loop
            params_copy = dict(params)
            params_copy["priority"] = "normal"
            return await self._handle_message_send(params_copy, token_id)

        channels = alert_doc.get("alert_channels", [])
        policy = alert_doc.get("escalation_policy", "first_available")

        for channel in channels:
            outbound_data = {"data": json.dumps({
                "method": "chat.send",
                "params": {
                    "composite_chat_id": channel["chat_id"],
                    "content": (
                        f"CRITICAL ALERT\n\n{params.get('subject', '')}\n\n"
                        f"{params.get('content', '')}"
                    ),
                    "from_agent": params.get("from_agent", "system"),
                    "priority": "critical",
                },
            })}
            await self._redis.stream_add("stream:chat:outbound", outbound_data)

            if policy == "first_available":
                break

        logger.info(
            "Escalation sent for %s: %d channel(s)", recipient, len(channels)
        )
        return {
            "escalated": True,
            "delivered": True,
            "duplicate": False,
            "message_id": params.get("message_id"),
            "channels": len(channels),
        }
