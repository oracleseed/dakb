"""
DAKB Messaging System - Notifications

Notification infrastructure for message delivery including
webhook support, polling endpoints, and message acknowledgment tracking.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Features:
- Webhook configuration and delivery
- Agent polling support
- Message acknowledgment tracking
- Notification preferences per agent
- Retry logic for failed webhooks
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from pymongo.collection import Collection

from .models import (
    Message,
    MessagePriority,
    NotificationPreferences,
    NotificationType,
    WebhookConfig,
    WebhookPayload,
)

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Webhook retry settings
WEBHOOK_TIMEOUT_SECONDS = 10
WEBHOOK_MAX_RETRIES = 3
WEBHOOK_RETRY_DELAYS = [5, 15, 30]  # Seconds between retries

# Notification batch settings
NOTIFICATION_BATCH_SIZE = 50
POLLING_DEFAULT_LIMIT = 20


# =============================================================================
# WEBHOOK MANAGER
# =============================================================================

class WebhookManager:
    """
    Manager for webhook configuration and delivery.

    Handles webhook registration, validation, delivery,
    and failure tracking.
    """

    def __init__(self, collection: Collection):
        """
        Initialize webhook manager.

        Args:
            collection: MongoDB collection for webhook configs
        """
        self.collection = collection
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=WEBHOOK_TIMEOUT_SECONDS,
                limits=httpx.Limits(
                    max_connections=50,
                    max_keepalive_connections=10,
                ),
            )
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    # =========================================================================
    # WEBHOOK CONFIGURATION
    # =========================================================================

    def _validate_webhook_url(self, url: str) -> tuple[bool, str]:
        """
        Validate webhook URL is not targeting internal networks (SSRF protection).

        ISS-065 Fix: Added SSRF protection for webhook URLs.

        Args:
            url: Webhook URL to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            parsed = urlparse(url)

            # Must be http or https
            if parsed.scheme not in ['http', 'https']:
                return False, "URL must use http or https scheme"

            hostname = parsed.hostname
            if hostname is None:
                return False, "Invalid URL: no hostname"

            # Block localhost variations
            blocked_hosts = ['localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]']
            if hostname.lower() in blocked_hosts:
                return False, "Localhost URLs not allowed for webhooks"

            # Block internal/private IP ranges
            try:
                ip = ipaddress.ip_address(hostname)
                if ip.is_private:
                    return False, "Private IP addresses not allowed"
                if ip.is_loopback:
                    return False, "Loopback addresses not allowed"
                if ip.is_reserved:
                    return False, "Reserved IP addresses not allowed"
                if ip.is_link_local:
                    return False, "Link-local addresses not allowed"
            except ValueError:
                # Not an IP address - hostname is OK
                pass

            # Block common internal hostnames
            internal_patterns = [
                'internal', 'intranet', 'corp', 'local',
                '10.', '172.16.', '172.17.', '172.18.', '172.19.',
                '172.20.', '172.21.', '172.22.', '172.23.', '172.24.',
                '172.25.', '172.26.', '172.27.', '172.28.', '172.29.',
                '172.30.', '172.31.', '192.168.'
            ]
            hostname_lower = hostname.lower()
            for pattern in internal_patterns:
                if pattern in hostname_lower:
                    return False, f"URL contains blocked pattern: {pattern}"

            return True, ""

        except Exception as e:
            return False, f"URL validation error: {str(e)}"

    def register_webhook(
        self,
        agent_id: str,
        url: str,
        secret: str | None = None,
        events: list[str] | None = None,
    ) -> WebhookConfig:
        """
        Register a webhook for an agent.

        Args:
            agent_id: Agent identifier
            url: Webhook URL endpoint
            secret: Shared secret for signature validation
            events: Events to trigger webhook

        Returns:
            Created webhook configuration

        Raises:
            ValueError: If URL validation fails (SSRF protection)
        """
        # ISS-065 Fix: Validate URL for SSRF protection
        is_valid, error_msg = self._validate_webhook_url(url)
        if not is_valid:
            raise ValueError(f"Invalid webhook URL: {error_msg}")

        webhook = WebhookConfig(
            agent_id=agent_id,
            url=url,
            secret=secret,
            events=events or ["message.received", "message.urgent"],
            enabled=True,
        )

        # Upsert - update if exists, insert if not
        self.collection.update_one(
            {"agent_id": agent_id, "url": url},
            {"$set": webhook.model_dump()},
            upsert=True
        )

        logger.info(f"Webhook registered for {agent_id}: {url}")
        return webhook

    def get_webhooks(self, agent_id: str) -> list[WebhookConfig]:
        """
        Get all webhooks for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            List of webhook configurations
        """
        cursor = self.collection.find({
            "agent_id": agent_id,
            "enabled": True
        })

        webhooks = []
        for doc in cursor:
            doc.pop("_id", None)
            webhooks.append(WebhookConfig(**doc))

        return webhooks

    def get_webhook_by_id(self, webhook_id: str) -> WebhookConfig | None:
        """
        Get webhook by ID.

        Args:
            webhook_id: Webhook identifier

        Returns:
            Webhook configuration or None
        """
        doc = self.collection.find_one({"webhook_id": webhook_id})
        if doc:
            doc.pop("_id", None)
            return WebhookConfig(**doc)
        return None

    def disable_webhook(self, webhook_id: str) -> bool:
        """
        Disable a webhook.

        Args:
            webhook_id: Webhook identifier

        Returns:
            True if disabled, False if not found
        """
        result = self.collection.update_one(
            {"webhook_id": webhook_id},
            {"$set": {"enabled": False}}
        )
        return result.modified_count > 0

    def delete_webhook(self, webhook_id: str) -> bool:
        """
        Delete a webhook.

        Args:
            webhook_id: Webhook identifier

        Returns:
            True if deleted, False if not found
        """
        result = self.collection.delete_one({"webhook_id": webhook_id})
        return result.deleted_count > 0

    # =========================================================================
    # WEBHOOK DELIVERY
    # =========================================================================

    async def deliver_webhook(
        self,
        webhook: WebhookConfig,
        payload: WebhookPayload,
    ) -> bool:
        """
        Deliver a webhook notification.

        Args:
            webhook: Webhook configuration
            payload: Payload to deliver

        Returns:
            True if successful, False otherwise
        """
        client = await self._get_client()

        # Build headers
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DAKB-Webhook/1.0",
            "X-DAKB-Event": payload.event,
            "X-DAKB-Timestamp": payload.timestamp.isoformat(),
        }

        # Sign payload if secret is configured
        body = json.dumps(payload.model_dump(), default=str)
        if webhook.secret:
            signature = self._sign_payload(body, webhook.secret)
            headers["X-DAKB-Signature"] = signature

        # Attempt delivery with retries
        last_error = None
        for attempt in range(WEBHOOK_MAX_RETRIES):
            try:
                response = await client.post(
                    webhook.url,
                    content=body,
                    headers=headers,
                )

                if response.status_code < 400:
                    # Success
                    self._record_success(webhook.webhook_id)
                    logger.debug(
                        f"Webhook delivered to {webhook.url} "
                        f"(status: {response.status_code})"
                    )
                    return True

                # Server error - may be temporary
                if response.status_code >= 500:
                    last_error = f"Server error: {response.status_code}"
                    logger.warning(
                        f"Webhook delivery failed (attempt {attempt + 1}): {last_error}"
                    )
                else:
                    # Client error - don't retry
                    last_error = f"Client error: {response.status_code}"
                    logger.error(f"Webhook rejected: {last_error}")
                    self._record_failure(webhook.webhook_id, last_error)
                    return False

            except httpx.TimeoutException:
                last_error = "Request timeout"
                logger.warning(
                    f"Webhook timeout (attempt {attempt + 1}): {webhook.url}"
                )

            except httpx.ConnectError as e:
                last_error = f"Connection error: {e}"
                logger.warning(
                    f"Webhook connection failed (attempt {attempt + 1}): {e}"
                )

            except Exception as e:
                last_error = str(e)
                logger.error(f"Webhook delivery error: {e}")

            # Wait before retry
            if attempt < WEBHOOK_MAX_RETRIES - 1:
                await asyncio.sleep(WEBHOOK_RETRY_DELAYS[attempt])

        # All retries failed
        self._record_failure(webhook.webhook_id, last_error)
        return False

    async def notify_message(
        self,
        message: Message,
        recipient_id: str,
    ) -> int:
        """
        Send webhook notifications for a message.

        Args:
            message: Message to notify about
            recipient_id: Recipient agent ID

        Returns:
            Number of webhooks delivered
        """
        webhooks = self.get_webhooks(recipient_id)
        if not webhooks:
            return 0

        # Build event type
        event = "message.received"
        if message.priority == MessagePriority.URGENT:
            event = "message.urgent"

        # Build payload
        payload = WebhookPayload(
            event=event,
            message_id=message.message_id,
            sender_id=message.sender_id,
            priority=message.priority,
            subject=message.subject,
            preview=message.content[:200] if len(message.content) > 200 else message.content,
        )

        # Deliver to all matching webhooks
        delivered = 0
        for webhook in webhooks:
            if event in webhook.events or "message.*" in webhook.events:
                if await self.deliver_webhook(webhook, payload):
                    delivered += 1

        return delivered

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _sign_payload(self, payload: str, secret: str) -> str:
        """
        Sign a payload using HMAC-SHA256.

        Args:
            payload: JSON payload string
            secret: Shared secret

        Returns:
            Hex-encoded signature
        """
        signature = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"sha256={signature}"

    def _record_success(self, webhook_id: str) -> None:
        """Record successful webhook delivery."""
        self.collection.update_one(
            {"webhook_id": webhook_id},
            {
                "$set": {
                    "last_triggered_at": datetime.utcnow(),
                    "failure_count": 0
                }
            }
        )

    def _record_failure(self, webhook_id: str, error: str | None) -> None:
        """Record failed webhook delivery and potentially disable."""
        result = self.collection.find_one_and_update(
            {"webhook_id": webhook_id},
            {
                "$set": {"last_triggered_at": datetime.utcnow()},
                "$inc": {"failure_count": 1}
            },
            return_document=True
        )

        if result:
            failure_count = result.get("failure_count", 0)
            max_failures = result.get("max_failures", 5)

            if failure_count >= max_failures:
                logger.warning(
                    f"Webhook {webhook_id} disabled after {failure_count} failures"
                )
                self.collection.update_one(
                    {"webhook_id": webhook_id},
                    {"$set": {"enabled": False}}
                )


# =============================================================================
# NOTIFICATION PREFERENCES MANAGER
# =============================================================================

class NotificationPreferencesManager:
    """
    Manager for agent notification preferences.

    Handles preference storage and retrieval for controlling
    how and when agents receive notifications.
    """

    def __init__(self, collection: Collection):
        """
        Initialize preferences manager.

        Args:
            collection: MongoDB collection for preferences
        """
        self.collection = collection

    def get_preferences(self, agent_id: str) -> NotificationPreferences:
        """
        Get notification preferences for an agent.

        Creates default preferences if not found.

        Args:
            agent_id: Agent identifier

        Returns:
            Notification preferences
        """
        doc = self.collection.find_one({"agent_id": agent_id})
        if doc:
            doc.pop("_id", None)
            return NotificationPreferences(**doc)

        # Return defaults
        return NotificationPreferences(agent_id=agent_id)

    def update_preferences(
        self,
        agent_id: str,
        preferences: NotificationPreferences,
    ) -> NotificationPreferences:
        """
        Update notification preferences for an agent.

        Args:
            agent_id: Agent identifier
            preferences: New preferences

        Returns:
            Updated preferences
        """
        preferences.agent_id = agent_id
        preferences.updated_at = datetime.utcnow()

        self.collection.update_one(
            {"agent_id": agent_id},
            {"$set": preferences.model_dump()},
            upsert=True
        )

        logger.info(f"Updated notification preferences for {agent_id}")
        return preferences

    def should_notify(
        self,
        agent_id: str,
        priority: MessagePriority,
    ) -> bool:
        """
        Check if an agent should receive immediate notification
        for a message with given priority.

        Args:
            agent_id: Agent identifier
            priority: Message priority

        Returns:
            True if should notify immediately
        """
        prefs = self.get_preferences(agent_id)

        # Check quiet hours
        if prefs.quiet_hours_start is not None and prefs.quiet_hours_end is not None:
            current_hour = datetime.utcnow().hour
            if prefs.quiet_hours_start <= current_hour < prefs.quiet_hours_end:
                # Only urgent messages during quiet hours
                return priority == MessagePriority.URGENT

        # Check priority threshold
        return prefs.priority_thresholds.get(priority.value, False)


# =============================================================================
# POLLING SERVICE
# =============================================================================

class PollingService:
    """
    Service for agent message polling.

    Provides endpoints for agents to poll for new messages
    and acknowledge receipt.
    """

    def __init__(
        self,
        messages_collection: Collection,
        acknowledgments_collection: Collection,
    ):
        """
        Initialize polling service.

        Args:
            messages_collection: MongoDB collection for messages
            acknowledgments_collection: MongoDB collection for acknowledgments
        """
        self.messages = messages_collection
        self.acks = acknowledgments_collection

    def poll_messages(
        self,
        agent_id: str,
        since: datetime | None = None,
        limit: int = POLLING_DEFAULT_LIMIT,
        priority_filter: MessagePriority | None = None,
    ) -> list[Message]:
        """
        Poll for new messages for an agent.

        Args:
            agent_id: Agent identifier
            since: Only return messages after this time
            limit: Maximum messages to return
            priority_filter: Filter by priority

        Returns:
            List of messages
        """
        from .models import MessageStatus, MessageType

        # Build query
        query: dict[str, Any] = {
            "$or": [
                {"recipient_id": agent_id},
                {"message_type": MessageType.BROADCAST.value}
            ],
            "status": {"$in": [
                MessageStatus.PENDING.value,
                MessageStatus.DELIVERED.value
            ]},
            "expires_at": {"$gt": datetime.utcnow()}
        }

        if since:
            query["created_at"] = {"$gt": since}

        if priority_filter:
            query["priority"] = priority_filter.value

        # Query with priority ordering
        cursor = self.messages.find(query).sort([
            ("priority", -1),  # URGENT first
            ("created_at", 1)  # Then oldest first
        ]).limit(limit)

        messages = []
        for doc in cursor:
            doc.pop("_id", None)
            messages.append(Message(**doc))

        return messages

    def acknowledge_message(
        self,
        message_id: str,
        agent_id: str,
        ack_type: str = "received",
    ) -> bool:
        """
        Acknowledge receipt of a message.

        Args:
            message_id: Message identifier
            agent_id: Acknowledging agent
            ack_type: Type of acknowledgment (received, read, processed)

        Returns:
            True if acknowledged, False if already acknowledged
        """
        # Check for existing acknowledgment
        existing = self.acks.find_one({
            "message_id": message_id,
            "agent_id": agent_id,
            "ack_type": ack_type
        })

        if existing:
            return False

        # Record acknowledgment
        self.acks.insert_one({
            "message_id": message_id,
            "agent_id": agent_id,
            "ack_type": ack_type,
            "acknowledged_at": datetime.utcnow()
        })

        # Update message status based on ack type
        from .models import MessageStatus

        if ack_type == "read":
            from .models import ReadReceipt
            receipt = ReadReceipt(agent_id=agent_id)
            self.messages.update_one(
                {"message_id": message_id},
                {
                    "$push": {"read_receipts": receipt.model_dump()},
                    "$set": {
                        "status": MessageStatus.READ.value,
                        "read_at": datetime.utcnow()
                    }
                }
            )
        elif ack_type == "received":
            from .models import DeliveryReceipt
            receipt = DeliveryReceipt(
                agent_id=agent_id,
                delivery_method=NotificationType.POLLING
            )
            self.messages.update_one(
                {"message_id": message_id},
                {
                    "$push": {"delivery_receipts": receipt.model_dump()},
                    "$set": {
                        "status": MessageStatus.DELIVERED.value,
                        "delivered_at": datetime.utcnow()
                    }
                }
            )

        logger.debug(f"Message {message_id} acknowledged ({ack_type}) by {agent_id}")
        return True

    def get_acknowledgments(
        self,
        message_id: str,
    ) -> list[dict]:
        """
        Get all acknowledgments for a message.

        Args:
            message_id: Message identifier

        Returns:
            List of acknowledgment records
        """
        cursor = self.acks.find({"message_id": message_id})
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results

    def get_unacknowledged_count(
        self,
        agent_id: str,
        ack_type: str = "received",
    ) -> int:
        """
        Get count of messages not yet acknowledged by an agent.

        Args:
            agent_id: Agent identifier
            ack_type: Type of acknowledgment to check

        Returns:
            Count of unacknowledged messages
        """
        from .models import MessageStatus, MessageType

        # Get acknowledged message IDs for this agent
        ack_cursor = self.acks.find(
            {"agent_id": agent_id, "ack_type": ack_type},
            {"message_id": 1}
        )
        acked_ids = [doc["message_id"] for doc in ack_cursor]

        # Count messages not in acknowledged list
        query = {
            "$or": [
                {"recipient_id": agent_id},
                {"message_type": MessageType.BROADCAST.value}
            ],
            "status": {"$ne": MessageStatus.EXPIRED.value},
            "expires_at": {"$gt": datetime.utcnow()},
            "message_id": {"$nin": acked_ids}
        }

        return self.messages.count_documents(query)


# =============================================================================
# NOTIFICATION SERVICE (Combined)
# =============================================================================

class NotificationService:
    """
    Combined notification service that manages both webhook and polling delivery.

    Coordinates message delivery through appropriate channels based on
    agent preferences and availability.
    """

    def __init__(
        self,
        messages_collection: Collection,
        webhooks_collection: Collection,
        preferences_collection: Collection,
        acknowledgments_collection: Collection,
    ):
        """
        Initialize notification service.

        Args:
            messages_collection: MongoDB collection for messages
            webhooks_collection: MongoDB collection for webhook configs
            preferences_collection: MongoDB collection for preferences
            acknowledgments_collection: MongoDB collection for acknowledgments
        """
        self.webhook_manager = WebhookManager(webhooks_collection)
        self.preferences_manager = NotificationPreferencesManager(preferences_collection)
        self.polling_service = PollingService(messages_collection, acknowledgments_collection)
        self.messages = messages_collection

    async def notify(
        self,
        message: Message,
        recipient_id: str,
    ) -> dict[str, Any]:
        """
        Send notification for a message using appropriate channels.

        Args:
            message: Message to notify about
            recipient_id: Recipient agent ID

        Returns:
            Notification result with delivery status
        """
        result = {
            "message_id": message.message_id,
            "recipient_id": recipient_id,
            "webhook_delivered": False,
            "polling_available": True,
        }

        # Check if should notify immediately
        should_notify = self.preferences_manager.should_notify(
            recipient_id,
            message.priority
        )

        if should_notify:
            # Attempt webhook delivery
            webhooks_delivered = await self.webhook_manager.notify_message(
                message,
                recipient_id
            )
            result["webhook_delivered"] = webhooks_delivered > 0
            result["webhooks_triggered"] = webhooks_delivered

        return result

    async def notify_broadcast(
        self,
        message: Message,
        agent_ids: list[str],
    ) -> dict[str, Any]:
        """
        Send notifications for a broadcast message to multiple agents.

        Args:
            message: Broadcast message
            agent_ids: List of recipient agent IDs

        Returns:
            Notification results for all recipients
        """
        results = {
            "message_id": message.message_id,
            "total_recipients": len(agent_ids),
            "webhook_success": 0,
            "webhook_failures": 0,
            "details": []
        }

        for agent_id in agent_ids:
            if agent_id == message.sender_id:
                continue  # Skip sender

            try:
                result = await self.notify(message, agent_id)
                results["details"].append(result)
                if result.get("webhook_delivered"):
                    results["webhook_success"] += 1
                else:
                    results["webhook_failures"] += 1
            except Exception as e:
                logger.error(f"Error notifying {agent_id}: {e}")
                results["webhook_failures"] += 1
                results["details"].append({
                    "recipient_id": agent_id,
                    "error": str(e)
                })

        return results

    async def close(self) -> None:
        """Close all connections."""
        await self.webhook_manager.close()
