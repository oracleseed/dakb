"""Data models for the Session Bridge."""
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class BridgeLink(BaseModel):
    """Links an agent session to an external chat."""
    session_id: str
    agent_id: str
    composite_chat_id: str          # "<platform>:<chat_id>" e.g. "telegram:12345"
    platform: str                   # "telegram", "whatsapp", "discord", etc.
    linked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    linked_by: str                  # Who created the link
    active: bool = True


class BridgeMessage(BaseModel):
    """A message flowing through the bridge."""
    msg_id: str = Field(default_factory=lambda: f"bridge_msg_{uuid.uuid4().hex[:12]}")
    session_id: str
    from_platform: str              # e.g. "telegram"
    from_user_id: str               # Platform user ID
    from_user_name: str             # Display name
    composite_chat_id: str
    content: str                    # Message text
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    msg_type: str = "user_message"  # user_message | system_event | agent_reply


class LaunchConfig(BaseModel):
    """Configuration for launching an agent from an external chat.

    SECURITY: agent launch from chat is DENY-BY-DEFAULT. It is only honored when
    `enabled` is True AND the requesting platform user id is present in
    `allowed_users`. Both fields are empty/disabled by default; populate them
    from your own configuration/environment, never hard-code identities here.
    """
    agent_id: str                           # Logical agent identifier to launch
    allowed_users: list[int] = Field(default_factory=list)  # Allowlist of platform user IDs
    launch_template: str                    # Template command string
    rate_limit_max: int = 1                 # Max launches per window
    rate_limit_window: int = 60             # Window in seconds
    enabled: bool = False                   # Disabled by default (deny by default)
    auto_bridge: bool = True                # Auto-start bridge on launch


class BridgeStatus(BaseModel):
    """Status response for a bridge session."""
    session_id: str
    bridge_online: bool                     # WS connected?
    heartbeat_age_seconds: float            # Time since last heartbeat
    linked_chats: list[str]                 # Composite chat IDs
    inbox_depth: int                        # Pending messages in Redis
    queue_depth: int                        # Offline queue in MongoDB
