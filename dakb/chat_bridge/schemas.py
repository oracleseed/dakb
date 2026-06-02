"""Pydantic schemas for the DAKB Chat Bridge.

Self-contained models for chat sessions, alert routing, and chat messages.
These are defined here (rather than in the shared ``db`` package) so the chat
bridge subsystem is independently importable and testable.

Collections:
- ``dakb_chat_sessions``  -> :class:`ChatSession`
- ``dakb_alert_config``   -> :class:`AlertConfig`
- ``dakb_chat_messages``  -> :class:`ChatMessage`
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# ENUMS
# =============================================================================

class ChatPlatform(str, Enum):
    """Supported chat platforms for the Chat Bridge."""
    TELEGRAM = "telegram"
    DISCORD = "discord"
    SLACK = "slack"
    WHATSAPP = "whatsapp"


class ConversationMode(str, Enum):
    """Conversation modes for chat sessions."""
    DIRECT = "direct"
    GROUP = "group"
    RELAY = "relay"


class RelayFilter(str, Enum):
    """Filter for which agent messages get relayed to chat."""
    ALL = "all"
    PRIORITY_HIGH = "priority_high"
    TAGGED_ONLY = "tagged_only"
    NONE = "none"


class EscalationPolicy(str, Enum):
    """Escalation policy for alert routing."""
    FIRST_AVAILABLE = "first_available"
    ALL = "all"


# =============================================================================
# EMBEDDED MODELS
# =============================================================================

class RelayConfig(BaseModel):
    """Relay configuration for chat sessions."""
    mirror_agent_messages: bool = Field(
        default=False, description="Show agent-to-agent messages in group"
    )
    allow_user_interrupt: bool = Field(
        default=True, description="User can jump into agent conversations"
    )
    relay_filter: RelayFilter = Field(
        default=RelayFilter.NONE, description="Filter for which messages get relayed"
    )


class AlertChannel(BaseModel):
    """Alert channel binding for a specific platform."""
    platform: ChatPlatform = Field(..., description="Platform for this alert channel")
    chat_id: str = Field(
        ..., min_length=1, description="Composite chat ID (e.g., 'telegram:111')"
    )


# =============================================================================
# COLLECTION SCHEMAS
# =============================================================================

class ChatSession(BaseModel):
    """Chat session binding — maps an external chat to DAKB agents.

    Collection: ``dakb_chat_sessions``
    """
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(..., min_length=1, max_length=100)
    platform: ChatPlatform = Field(...)
    composite_chat_id: str = Field(..., min_length=3)
    external_chat_id: str = Field(..., min_length=1)
    external_user_id: str = Field(..., min_length=1)
    invited_agents: list[str] = Field(default_factory=list)
    conversation_mode: ConversationMode = Field(default=ConversationMode.DIRECT)
    relay_config: RelayConfig = Field(default_factory=RelayConfig)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlertConfig(BaseModel):
    """User alert configuration — routes DAKB alerts to external chat channels.

    Collection: ``dakb_alert_config``
    """
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(..., min_length=1, max_length=100)
    alert_channels: list[AlertChannel] = Field(default_factory=list)
    escalation_policy: EscalationPolicy = Field(default=EscalationPolicy.FIRST_AVAILABLE)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatMessage(BaseModel):
    """Chat message record — tracks messages flowing between external chat and DAKB.

    Collection: ``dakb_chat_messages``
    """
    model_config = ConfigDict(populate_by_name=True)

    message_id: str = Field(..., min_length=1, max_length=100)
    source_platform: ChatPlatform = Field(...)
    external_user_id: str | None = Field(None)
    external_chat_id: str = Field(..., min_length=1)
    composite_chat_id: str = Field(..., min_length=3)
    content: str = Field(...)
    content_type: str = Field(default="text")
    direction: str = Field(...)
    sender_agent: str | None = Field(None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = [
    "ChatPlatform",
    "ConversationMode",
    "RelayFilter",
    "EscalationPolicy",
    "RelayConfig",
    "AlertChannel",
    "ChatSession",
    "AlertConfig",
    "ChatMessage",
]
