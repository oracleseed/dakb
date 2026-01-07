"""
DAKB Messaging System - Models

Pydantic models for inter-agent messaging with priority queue support,
broadcast messaging, and notification infrastructure.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Features:
- Direct messaging between agents
- Broadcast messaging to all agents
- Priority-based message queue
- Message threading and replies
- Automatic message expiration
- Read/delivery acknowledgments
"""

import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# =============================================================================
# ENUMS
# =============================================================================

class MessageType(str, Enum):
    """Message types for inter-agent communication."""
    DIRECT = "direct"           # Direct message to specific agent
    BROADCAST = "broadcast"     # Broadcast to all agents
    REPLY = "reply"             # Reply to existing message
    SYSTEM = "system"           # System-generated message


class MessagePriority(str, Enum):
    """Message priority levels for queue processing."""
    LOW = "low"             # Processed last, non-critical
    NORMAL = "normal"       # Standard processing
    HIGH = "high"           # Prioritized processing
    URGENT = "urgent"       # Immediate processing, triggers notifications


class MessageStatus(str, Enum):
    """Message delivery and read status."""
    PENDING = "pending"         # Created but not yet delivered
    DELIVERED = "delivered"     # Delivered to recipient's inbox
    READ = "read"               # Acknowledged as read by recipient
    EXPIRED = "expired"         # Past expiration time, auto-archived


class NotificationType(str, Enum):
    """Notification delivery methods."""
    WEBHOOK = "webhook"         # HTTP POST to registered endpoint
    POLLING = "polling"         # Agent polls for messages
    WEBSOCKET = "websocket"     # Real-time WebSocket push


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def generate_message_id() -> str:
    """Generate a unique message identifier with timestamp prefix."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique = uuid.uuid4().hex[:8]
    return f"msg_{timestamp}_{unique}"


def generate_thread_id() -> str:
    """Generate a unique thread identifier."""
    timestamp = datetime.utcnow().strftime("%Y%m%d")
    unique = uuid.uuid4().hex[:8]
    return f"thread_{timestamp}_{unique}"


# =============================================================================
# EMBEDDED MODELS
# =============================================================================

class MessageAttachment(BaseModel):
    """Attachment for messages (JSON data, file references, etc.)."""
    attachment_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex[:8],
        description="Unique attachment identifier"
    )
    type: str = Field(..., description="Attachment type (json, file_ref, code, etc.)")
    name: str = Field(..., max_length=100, description="Attachment name")
    content: Any = Field(..., description="Attachment content or reference")
    size_bytes: int | None = Field(None, ge=0, description="Content size for validation")
    mime_type: str | None = Field(None, description="MIME type if applicable")

    @field_validator('size_bytes')
    @classmethod
    def validate_size(cls, v: int | None) -> int | None:
        """Validate attachment size does not exceed 1MB limit."""
        if v is not None and v > 1024 * 1024:  # 1MB limit
            raise ValueError("Attachment size exceeds 1MB limit")
        return v


class DeliveryReceipt(BaseModel):
    """Delivery acknowledgment record."""
    agent_id: str = Field(..., description="Agent that received the message")
    delivered_at: datetime = Field(default_factory=datetime.utcnow)
    delivery_method: NotificationType = Field(
        default=NotificationType.POLLING,
        description="How the message was delivered"
    )


class ReadReceipt(BaseModel):
    """Read acknowledgment record."""
    agent_id: str = Field(..., description="Agent that read the message")
    read_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# MAIN MESSAGE MODEL
# =============================================================================

class Message(BaseModel):
    """
    Core message model for inter-agent communication.

    Supports direct messaging, broadcasts, replies, and system messages
    with priority-based queue processing.
    """
    message_id: str = Field(
        default_factory=generate_message_id,
        description="Unique message identifier"
    )

    # Routing
    sender_id: str = Field(..., description="Sending agent ID")
    recipient_id: str | None = Field(
        None,
        description="Target agent ID (None for broadcast)"
    )
    original_recipient: str | None = Field(
        None,
        description="Original recipient value if alias was resolved (Phase 3 Token Alias System)"
    )

    # Message type and priority
    message_type: MessageType = Field(
        default=MessageType.DIRECT,
        description="Type of message"
    )
    priority: MessagePriority = Field(
        default=MessagePriority.NORMAL,
        description="Message priority for queue processing"
    )
    priority_score: int = Field(
        default=10,
        description="Numeric priority for MongoDB sorting (ISS-071 Fix)"
    )

    # Content
    subject: str = Field(..., max_length=200, description="Message subject line")
    content: str = Field(..., max_length=100000, description="Message body content (max 100KB)")
    # ISS-063 Fix: Added max_length=100000 to prevent DoS via arbitrarily large message bodies
    attachments: list[MessageAttachment] = Field(
        default_factory=list,
        description="Message attachments"
    )

    # Threading
    thread_id: str | None = Field(
        None,
        description="Thread ID for message grouping"
    )
    reply_to_id: str | None = Field(
        None,
        description="Message ID this is replying to"
    )

    # Delivery status
    status: MessageStatus = Field(
        default=MessageStatus.PENDING,
        description="Current message status"
    )
    delivery_receipts: list[DeliveryReceipt] = Field(
        default_factory=list,
        description="Delivery acknowledgments"
    )
    read_receipts: list[ReadReceipt] = Field(
        default_factory=list,
        description="Read acknowledgments"
    )

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    delivered_at: datetime | None = Field(None, description="First delivery time")
    read_at: datetime | None = Field(None, description="First read time")
    expires_at: datetime = Field(
        default_factory=lambda: datetime.utcnow() + timedelta(days=7),
        description="Auto-expiration time (default 7 days)"
    )

    # Metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )

    @model_validator(mode='after')
    def validate_routing_and_set_priority_score(self) -> 'Message':
        """
        Validate message routing based on type and set priority score.

        ISS-071 Fix: Set numeric priority_score for correct MongoDB sorting.
        """
        if self.message_type == MessageType.DIRECT and self.recipient_id is None:
            raise ValueError("Direct messages must have a recipient_id")
        if self.message_type == MessageType.REPLY and self.reply_to_id is None:
            raise ValueError("Reply messages must have a reply_to_id")

        # ISS-071 Fix: Set priority_score based on priority enum
        priority_scores = {
            MessagePriority.URGENT: 1000,
            MessagePriority.HIGH: 100,
            MessagePriority.NORMAL: 10,
            MessagePriority.LOW: 1
        }
        self.priority_score = priority_scores.get(self.priority, 10)

        return self

    @field_validator('attachments')
    @classmethod
    def validate_attachments_count(cls, v: list[MessageAttachment]) -> list[MessageAttachment]:
        """Limit attachments to 10 per message."""
        if len(v) > 10:
            raise ValueError("Maximum 10 attachments allowed per message")
        return v

    def is_expired(self) -> bool:
        """Check if message has expired."""
        return datetime.utcnow() > self.expires_at

    def mark_delivered(self, agent_id: str, method: NotificationType = NotificationType.POLLING) -> None:
        """Mark message as delivered to an agent."""
        if not any(r.agent_id == agent_id for r in self.delivery_receipts):
            self.delivery_receipts.append(DeliveryReceipt(
                agent_id=agent_id,
                delivery_method=method
            ))
            if self.status == MessageStatus.PENDING:
                self.status = MessageStatus.DELIVERED
                self.delivered_at = datetime.utcnow()

    def mark_read(self, agent_id: str) -> None:
        """Mark message as read by an agent."""
        if not any(r.agent_id == agent_id for r in self.read_receipts):
            self.read_receipts.append(ReadReceipt(agent_id=agent_id))
            if self.status in [MessageStatus.PENDING, MessageStatus.DELIVERED]:
                self.status = MessageStatus.READ
                self.read_at = datetime.utcnow()

    class Config:
        json_schema_extra = {
            "example": {
                "sender_id": "backend-agent",
                "recipient_id": "mlx-agent",
                "message_type": "direct",
                "priority": "normal",
                "subject": "Training Complete Notification",
                "content": "The PPO model training has completed with 89% success rate.",
                "thread_id": None,
                "reply_to_id": None
            }
        }


# =============================================================================
# CREATE/UPDATE MODELS
# =============================================================================

class MessageCreate(BaseModel):
    """Schema for creating new messages."""
    recipient_id: str | None = Field(
        None,
        description="Target agent ID (None for broadcast)"
    )
    message_type: MessageType = Field(
        default=MessageType.DIRECT,
        description="Type of message"
    )
    priority: MessagePriority = Field(
        default=MessagePriority.NORMAL,
        description="Message priority"
    )
    subject: str = Field(..., max_length=200, description="Message subject")
    content: str = Field(..., max_length=100000, description="Message body (max 100KB)")
    # ISS-063 Fix: Added max_length to prevent DoS
    attachments: list[MessageAttachment] = Field(
        default_factory=list,
        description="Message attachments"
    )
    thread_id: str | None = Field(None, description="Thread to add message to")
    reply_to_id: str | None = Field(None, description="Message being replied to")
    expires_in_hours: int = Field(
        default=168,  # 7 days
        ge=1,
        le=8760,  # Max 1 year
        description="Hours until message expires"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_message_type(self) -> 'MessageCreate':
        """Validate message type requirements."""
        if self.message_type == MessageType.DIRECT and self.recipient_id is None:
            raise ValueError("Direct messages must specify recipient_id")
        if self.message_type == MessageType.REPLY and self.reply_to_id is None:
            raise ValueError("Reply messages must specify reply_to_id")
        if self.message_type == MessageType.BROADCAST and self.recipient_id is not None:
            raise ValueError("Broadcast messages should not have recipient_id")
        return self


class MessageFilter(BaseModel):
    """Filter criteria for message queries."""
    sender_id: str | None = Field(None, description="Filter by sender")
    recipient_id: str | None = Field(None, description="Filter by recipient")
    message_type: MessageType | None = Field(None, description="Filter by type")
    priority: MessagePriority | None = Field(None, description="Filter by priority")
    status: MessageStatus | None = Field(None, description="Filter by status")
    thread_id: str | None = Field(None, description="Filter by thread")
    since: datetime | None = Field(None, description="Messages after this time")
    before: datetime | None = Field(None, description="Messages before this time")
    include_expired: bool = Field(default=False, description="Include expired messages")


class MessageUpdate(BaseModel):
    """Schema for updating message status."""
    status: MessageStatus | None = Field(None, description="New status")


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class MessageResponse(BaseModel):
    """Response wrapper for single message."""
    success: bool = Field(default=True)
    message: Message | None = Field(None)
    error: str | None = Field(None)


class MessageListResponse(BaseModel):
    """Response wrapper for message list queries."""
    success: bool = Field(default=True)
    messages: list[Message] = Field(default_factory=list)
    total: int = Field(default=0)
    page: int = Field(default=1)
    page_size: int = Field(default=20)
    has_more: bool = Field(default=False)
    error: str | None = Field(None)


class BroadcastResponse(BaseModel):
    """Response for broadcast message operations."""
    success: bool = Field(default=True)
    message_id: str = Field(..., description="Broadcast message ID")
    recipients_count: int = Field(default=0, description="Number of recipients")
    delivered_count: int = Field(default=0, description="Number delivered")
    error: str | None = Field(None)


class MessageStats(BaseModel):
    """Message statistics for an agent."""
    agent_id: str = Field(..., description="Agent identifier")
    total_sent: int = Field(default=0, ge=0)
    total_received: int = Field(default=0, ge=0)
    unread_count: int = Field(default=0, ge=0)
    pending_count: int = Field(default=0, ge=0)
    by_priority: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)


# =============================================================================
# WEBHOOK/NOTIFICATION MODELS
# =============================================================================

class WebhookConfig(BaseModel):
    """Webhook configuration for message notifications."""
    webhook_id: str = Field(
        default_factory=lambda: f"webhook_{uuid.uuid4().hex[:8]}",
        description="Unique webhook identifier"
    )
    agent_id: str = Field(..., description="Owning agent ID")
    url: str = Field(..., description="Webhook URL endpoint")
    secret: str | None = Field(None, description="Shared secret for validation")
    events: list[str] = Field(
        default_factory=lambda: ["message.received", "message.urgent"],
        description="Events to trigger webhook"
    )
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_triggered_at: datetime | None = Field(None)
    failure_count: int = Field(default=0, ge=0)
    max_failures: int = Field(default=5, description="Disable after this many failures")


class WebhookPayload(BaseModel):
    """Payload sent to webhook endpoints."""
    event: str = Field(..., description="Event type")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    message_id: str = Field(..., description="Related message ID")
    sender_id: str = Field(..., description="Message sender")
    priority: MessagePriority = Field(..., description="Message priority")
    subject: str = Field(..., description="Message subject")
    preview: str = Field(..., max_length=200, description="Content preview")


class NotificationPreferences(BaseModel):
    """Agent notification preferences."""
    agent_id: str = Field(..., description="Agent identifier")
    enable_webhooks: bool = Field(default=True)
    enable_polling: bool = Field(default=True)
    priority_thresholds: dict[str, bool] = Field(
        default_factory=lambda: {
            "urgent": True,
            "high": True,
            "normal": False,
            "low": False
        },
        description="Which priorities trigger immediate notifications"
    )
    quiet_hours_start: int | None = Field(None, ge=0, le=23, description="Hour to start quiet period")
    quiet_hours_end: int | None = Field(None, ge=0, le=23, description="Hour to end quiet period")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# QUEUE MODELS
# =============================================================================

class QueuedMessage(BaseModel):
    """Message in the priority queue."""
    message_id: str = Field(..., description="Message identifier")
    priority: MessagePriority = Field(..., description="Queue priority")
    priority_score: int = Field(
        ...,
        description="Numeric priority for sorting (higher = more urgent)"
    )
    enqueued_at: datetime = Field(default_factory=datetime.utcnow)
    attempts: int = Field(default=0, ge=0, description="Delivery attempts")
    next_attempt_at: datetime | None = Field(None, description="Next delivery attempt time")
    last_error: str | None = Field(None, description="Last delivery error")

    @staticmethod
    def priority_to_score(priority: MessagePriority) -> int:
        """Convert priority enum to numeric score for sorting."""
        scores = {
            MessagePriority.URGENT: 1000,
            MessagePriority.HIGH: 100,
            MessagePriority.NORMAL: 10,
            MessagePriority.LOW: 1
        }
        return scores.get(priority, 10)


class QueueStats(BaseModel):
    """Queue statistics."""
    total_pending: int = Field(default=0, ge=0)
    by_priority: dict[str, int] = Field(default_factory=dict)
    oldest_message_age_seconds: float | None = Field(None)
    processing_rate_per_minute: float = Field(default=0.0)
