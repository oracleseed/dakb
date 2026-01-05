"""
DAKB Messaging System

Inter-agent messaging infrastructure with priority queues,
broadcast support, and notification delivery.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Modules:
- models: Pydantic models for messages, queues, and notifications
- api: Repository for message CRUD operations
- queue: Priority-based message queue with MongoDB backing
- notifications: Webhook and polling notification infrastructure

Usage:
    from backend.dakb_service.messaging import (
        MessageRepository,
        MessageQueue,
        NotificationService,
        Message,
        MessageCreate,
        MessagePriority,
        MessageStatus,
    )
"""

from .models import (
    # Enums
    MessageType,
    MessagePriority,
    MessageStatus,
    NotificationType,
    # Main models
    Message,
    MessageAttachment,
    DeliveryReceipt,
    ReadReceipt,
    # Create/Update models
    MessageCreate,
    MessageFilter,
    MessageUpdate,
    # Response models
    MessageResponse,
    MessageListResponse,
    BroadcastResponse,
    MessageStats,
    # Queue models
    QueuedMessage,
    QueueStats,
    # Notification models
    WebhookConfig,
    WebhookPayload,
    NotificationPreferences,
    # Helper functions
    generate_message_id,
    generate_thread_id,
)

from .api import MessageRepository

from .queue import (
    MessageQueue,
    AsyncQueueProcessor,
    QueueItem,
    PRIORITY_SCORES,
    MAX_DELIVERY_ATTEMPTS,
)

from .notifications import (
    WebhookManager,
    NotificationPreferencesManager,
    PollingService,
    NotificationService,
)

__all__ = [
    # Enums
    "MessageType",
    "MessagePriority",
    "MessageStatus",
    "NotificationType",
    # Main models
    "Message",
    "MessageAttachment",
    "DeliveryReceipt",
    "ReadReceipt",
    # Create/Update models
    "MessageCreate",
    "MessageFilter",
    "MessageUpdate",
    # Response models
    "MessageResponse",
    "MessageListResponse",
    "BroadcastResponse",
    "MessageStats",
    # Queue models
    "QueuedMessage",
    "QueueStats",
    # Notification models
    "WebhookConfig",
    "WebhookPayload",
    "NotificationPreferences",
    # Helper functions
    "generate_message_id",
    "generate_thread_id",
    # Repositories/Services
    "MessageRepository",
    "MessageQueue",
    "AsyncQueueProcessor",
    "QueueItem",
    "WebhookManager",
    "NotificationPreferencesManager",
    "PollingService",
    "NotificationService",
    # Constants
    "PRIORITY_SCORES",
    "MAX_DELIVERY_ATTEMPTS",
]
