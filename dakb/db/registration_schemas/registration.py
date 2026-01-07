"""
DAKB Registration System Schemas

Pydantic v2 models for invite-only agent registration system.
These models support the self-registration workflow with admin-generated invite tokens.

Version: 1.0
Created: 2025-12-11
Author: Backend Agent (Claude Opus 4.5)

Collections:
- dakb_invite_tokens: Invite tokens for agent registration
- dakb_registration_audit: Registration-specific audit trail (90-day TTL)

Session Reference: sess_selfreg_v1_20251211

Note: This module is intentionally self-contained to avoid circular imports.
It re-defines the enums it needs (AgentType, AgentRole, AccessLevel) rather
than importing them from the parent schemas module.
"""

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# TIMEZONE-AWARE DATETIME HELPER
# =============================================================================

def utcnow() -> datetime:
    """
    Get current UTC time with timezone info.

    This replaces the deprecated datetime.utcnow() which returns naive datetime.
    Using datetime.now(timezone.utc) returns timezone-aware datetime.

    Returns:
        Current UTC time as timezone-aware datetime
    """
    return datetime.now(timezone.utc)


# =============================================================================
# LOCAL ENUMS (avoid circular imports with parent schemas)
# =============================================================================

class AgentType(str, Enum):
    """Types of AI agents/LLMs (local copy for circular import avoidance)."""
    CLAUDE = "claude"
    CLAUDE_CODE = "claude_code"
    GPT = "gpt"
    OPENAI = "openai"
    GEMINI = "gemini"
    GROK = "grok"
    LOCAL = "local"
    HUMAN = "human"


class AgentRole(str, Enum):
    """Agent permission roles (local copy for circular import avoidance)."""
    ADMIN = "admin"
    DEVELOPER = "developer"
    ANALYST = "analyst"
    OBSERVER = "observer"


class AccessLevel(str, Enum):
    """3-tier access control levels (local copy for circular import avoidance)."""
    PUBLIC = "public"
    RESTRICTED = "restricted"
    SECRET = "secret"


# =============================================================================
# REGISTRATION-SPECIFIC ENUMS
# =============================================================================

class InviteTokenStatus(str, Enum):
    """
    Invite token lifecycle status.

    Tokens can only transition in one direction:
    ACTIVE -> USED (on successful registration)
    ACTIVE -> EXPIRED (on time expiry)
    ACTIVE -> REVOKED (by admin action)
    """
    ACTIVE = "active"
    USED = "used"
    EXPIRED = "expired"
    REVOKED = "revoked"


class RegistrationAuditAction(str, Enum):
    """
    Registration-specific audit action types.

    These are distinct from the main AuditAction enum to enable
    specialized registration audit queries.
    """
    INVITE_CREATED = "invite_created"
    INVITE_USED = "invite_used"
    INVITE_EXPIRED = "invite_expired"
    INVITE_REVOKED = "invite_revoked"
    AGENT_REGISTERED = "agent_registered"
    AGENT_REVOKED = "agent_revoked"
    REGISTRATION_FAILED = "registration_failed"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def generate_invite_token() -> str:
    """
    Generate a cryptographically secure invite token.

    Format: inv_{YYYYMMDD}_{12-hex-chars}
    Example: inv_20251211_a1b2c3d4e5f6

    Uses secrets.token_hex() for cryptographic randomness (48 bits entropy).

    Returns:
        Formatted invite token string
    """
    timestamp = utcnow().strftime("%Y%m%d")
    random_part = secrets.token_hex(6)  # 12 hex chars = 48 bits entropy
    return f"inv_{timestamp}_{random_part}"


def generate_audit_id() -> str:
    """
    Generate unique ID for registration audit entries.

    Format: regaudit_{YYYYMMDD}_{8-hex-chars}

    Returns:
        Formatted audit ID string
    """
    timestamp = utcnow().strftime("%Y%m%d")
    random_part = uuid.uuid4().hex[:8]
    return f"regaudit_{timestamp}_{random_part}"


# =============================================================================
# MAIN SCHEMAS
# =============================================================================

class DakbInviteToken(BaseModel):
    """
    Invite token for agent registration.

    Tokens are single-use and created by admin agents.
    Format: inv_{timestamp}_{random} (e.g., inv_20251211_abc12345678)

    Lifecycle:
    1. Admin creates token with purpose and optional constraints
    2. Token is shared with intended agent
    3. Agent uses token to register (token becomes USED)
    4. Token cannot be reused

    Attributes:
        invite_token: Unique token identifier (cryptographically secure)
        created_by: Admin agent ID who generated the token
        created_at: Token creation timestamp
        for_agent_type: Expected agent type (optional validation)
        for_agent_id_hint: Suggested agent ID for the invitee
        purpose: Required explanation for why this invite was created
        granted_role: Role that will be assigned to registered agent
        granted_access_levels: Access levels that will be granted
        pre_registered_alias: Alias to auto-register for the new agent
        pre_registered_alias_role: Role metadata for the auto-registered alias
        status: Current token status
        expires_at: When the token becomes invalid
        used_by_agent_id: Agent that consumed this token
        used_at: When the token was used
        admin_notes: Optional private notes from admin
    """
    invite_token: str = Field(
        default_factory=generate_invite_token,
        description="Unique invite token (format: inv_YYYYMMDD_xxxxxxxxxxxx)"
    )

    # Who created
    created_by: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Admin agent ID who created this token"
    )
    created_at: datetime = Field(
        default_factory=utcnow,
        description="Token creation timestamp"
    )

    # Target constraints (optional)
    for_agent_type: Optional[AgentType] = Field(
        None,
        description="Expected agent type for validation (optional)"
    )
    for_agent_id_hint: Optional[str] = Field(
        None,
        max_length=50,
        description="Suggested agent ID for the invitee (optional)"
    )
    purpose: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Required: Why this invite was created"
    )

    # Access configuration
    granted_role: AgentRole = Field(
        default=AgentRole.DEVELOPER,
        description="Role to grant upon registration"
    )
    granted_access_levels: list[AccessLevel] = Field(
        default_factory=lambda: [AccessLevel.PUBLIC],
        description="Access levels to grant upon registration"
    )

    # Alias pre-registration (optional)
    pre_registered_alias: Optional[str] = Field(
        None,
        max_length=50,
        description="Alias to auto-register for the new agent"
    )
    pre_registered_alias_role: Optional[str] = Field(
        None,
        max_length=100,
        description="Role metadata for the auto-registered alias"
    )

    # Status
    status: InviteTokenStatus = Field(
        default=InviteTokenStatus.ACTIVE,
        description="Current token status"
    )
    expires_at: datetime = Field(
        default_factory=lambda: utcnow() + timedelta(hours=72),
        description="Token expiration time (default: 72 hours)"
    )

    # Usage tracking
    used_by_agent_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Agent that used this token (set on registration)"
    )
    used_at: Optional[datetime] = Field(
        None,
        description="When the token was consumed (set on registration)"
    )

    # Notes
    admin_notes: Optional[str] = Field(
        None,
        max_length=500,
        description="Private notes from admin (not shared with invitee)"
    )

    @field_validator('invite_token')
    @classmethod
    def validate_token_format(cls, v: str) -> str:
        """Validate invite token follows expected format."""
        pattern = r'^inv_[0-9]{8}_[a-f0-9]{12}$'
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid token format. Expected: inv_YYYYMMDD_xxxxxxxxxxxx, got: {v}"
            )
        return v

    @field_validator('granted_access_levels')
    @classmethod
    def validate_access_levels(cls, v: list[AccessLevel]) -> list[AccessLevel]:
        """Ensure at least one access level is granted."""
        if not v:
            return [AccessLevel.PUBLIC]
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "invite_token": "inv_20251211_abc12345678",
                "created_by": "Coordinator",
                "for_agent_type": "gemini",
                "for_agent_id_hint": "gemini-research-v1",
                "purpose": "Research assistant for trading analysis",
                "granted_role": "developer",
                "granted_access_levels": ["public"],
                "expires_at": "2025-12-14T12:00:00Z",
                "status": "active"
            }
        }


class DakbRegistrationAudit(BaseModel):
    """
    Audit log for registration events.

    Separate from main audit log for registration-specific queries
    and to maintain a clean audit trail for compliance purposes.

    TTL: 90 days (enforced via MongoDB TTL index)

    Attributes:
        audit_id: Unique audit entry identifier
        timestamp: When the event occurred
        action: Type of registration action
        actor_agent_id: Agent that performed the action
        actor_ip: IP address if available (for security monitoring)
        target_token: Invite token involved (if applicable)
        target_agent_id: Agent affected by the action
        details: Additional context as key-value pairs
        success: Whether the action succeeded
        error_message: Error details if action failed
        expires_at: When this audit entry will be auto-deleted (90 days)
    """
    audit_id: str = Field(
        default_factory=generate_audit_id,
        description="Unique audit entry ID (format: regaudit_YYYYMMDD_xxxxxxxx)"
    )

    timestamp: datetime = Field(
        default_factory=utcnow,
        description="Event timestamp"
    )
    action: RegistrationAuditAction = Field(
        ...,
        description="Type of registration action"
    )

    # Actor
    actor_agent_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Agent that performed this action"
    )
    actor_ip: Optional[str] = Field(
        None,
        max_length=45,  # IPv6 max length
        description="IP address of the actor (for security monitoring)"
    )

    # Target
    target_token: Optional[str] = Field(
        None,
        description="Invite token involved in this action"
    )
    target_agent_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Agent ID affected by this action"
    )

    # Details
    details: dict = Field(
        default_factory=dict,
        description="Additional context as key-value pairs"
    )
    success: bool = Field(
        default=True,
        description="Whether the action succeeded"
    )
    error_message: Optional[str] = Field(
        None,
        max_length=500,
        description="Error message if action failed"
    )

    # TTL - 90 days expiry
    expires_at: datetime = Field(
        default_factory=lambda: utcnow() + timedelta(days=90),
        description="When this audit entry expires (90-day TTL)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "audit_id": "regaudit_20251211_abc12345",
                "timestamp": "2025-12-11T10:00:00Z",
                "action": "agent_registered",
                "actor_agent_id": "gemini-research-v1",
                "target_token": "inv_20251211_abc12345678",
                "target_agent_id": "gemini-research-v1",
                "details": {
                    "agent_type": "gemini",
                    "display_name": "Gemini Research Assistant",
                    "created_by_invite_from": "Coordinator"
                },
                "success": True,
                "expires_at": "2026-03-11T10:00:00Z"
            }
        }


# =============================================================================
# CREATE/UPDATE MODELS (Input validation)
# =============================================================================

class InviteTokenCreate(BaseModel):
    """
    Schema for creating invite tokens (admin endpoint).

    Used by POST /api/v1/register/invite endpoint.
    """
    for_agent_type: Optional[AgentType] = Field(
        None,
        description="Expected agent type for validation"
    )
    for_agent_id_hint: Optional[str] = Field(
        None,
        max_length=50,
        description="Suggested agent ID for the invitee"
    )
    purpose: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Required: Why this invite is being created"
    )
    granted_role: AgentRole = Field(
        default=AgentRole.DEVELOPER,
        description="Role to grant upon registration"
    )
    granted_access_levels: list[AccessLevel] = Field(
        default_factory=lambda: [AccessLevel.PUBLIC],
        description="Access levels to grant"
    )
    pre_registered_alias: Optional[str] = Field(
        None,
        max_length=50,
        description="Alias to auto-register for the new agent"
    )
    pre_registered_alias_role: Optional[str] = Field(
        None,
        max_length=100,
        description="Role for the auto-registered alias"
    )
    expires_in_hours: int = Field(
        default=72,
        ge=1,
        le=168,  # Max 7 days
        description="Token validity in hours (1-168, default: 72)"
    )
    admin_notes: Optional[str] = Field(
        None,
        max_length=500,
        description="Private notes (not shared with invitee)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "purpose": "Research assistant for trading analysis tasks",
                "for_agent_type": "gemini",
                "for_agent_id_hint": "gemini-research-v1",
                "granted_role": "developer",
                "expires_in_hours": 72
            }
        }


class RegistrationRequest(BaseModel):
    """
    Schema for agent registration request with invite token.

    Used by POST /api/v1/register/request endpoint.
    """
    invite_token: str = Field(
        ...,
        description="Invite token from admin (format: inv_YYYYMMDD_xxxxxxxxxxxx)"
    )
    agent_id: str = Field(
        ...,
        min_length=4,
        max_length=50,
        description="Unique agent identifier (lowercase, hyphens allowed)"
    )
    agent_type: AgentType = Field(
        ...,
        description="Type of AI agent"
    )
    display_name: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Human-readable display name"
    )
    machine_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Machine identifier where agent runs"
    )
    purpose: str = Field(
        ...,
        min_length=20,
        max_length=500,
        description="What this agent does and why it needs DAKB access"
    )
    alias: Optional[str] = Field(
        None,
        max_length=50,
        description="Optional alias to register (e.g., 'Coordinator', 'Reviewer')"
    )
    alias_role: Optional[str] = Field(
        None,
        max_length=100,
        description="Role for the alias (e.g., 'orchestration', 'code_review')"
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Agent capabilities (coding, debugging, etc.)"
    )
    model_version: Optional[str] = Field(
        None,
        max_length=50,
        description="LLM model version (e.g., 'claude-opus-4-5', 'gpt-5.1')"
    )

    @field_validator('invite_token')
    @classmethod
    def validate_token_format(cls, v: str) -> str:
        """Validate invite token follows expected format."""
        pattern = r'^inv_[0-9]{8}_[a-f0-9]{12}$'
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid token format. Expected: inv_YYYYMMDD_xxxxxxxxxxxx, got: {v}"
            )
        return v

    @field_validator('agent_id')
    @classmethod
    def validate_agent_id_format(cls, v: str) -> str:
        """
        Validate agent_id follows naming convention.

        Rules:
        - Lowercase alphanumeric with hyphens
        - Must start and end with alphanumeric
        - Length: 4-50 characters
        """
        pattern = r'^[a-z0-9][a-z0-9-]{2,48}[a-z0-9]$'
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid agent_id format. Must be lowercase alphanumeric with hyphens, "
                f"4-50 chars, start/end with alphanumeric. Got: {v}"
            )
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "invite_token": "inv_20251211_abc12345678",
                "agent_id": "gemini-research-v1",
                "agent_type": "gemini",
                "display_name": "Gemini Research Assistant",
                "machine_id": "gcp-instance-1",
                "purpose": "Research assistant for analyzing trading strategies and market data",
                "capabilities": ["research", "analysis", "summarization"],
                "model_version": "gemini-3-pro"
            }
        }


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class InviteTokenResponse(BaseModel):
    """Response for invite token creation."""
    invite_token: str
    expires_at: datetime
    for_agent_type: Optional[str] = None
    for_agent_id_hint: Optional[str] = None
    granted_role: str
    granted_access_levels: list[str]
    pre_registered_alias: Optional[str] = None
    created_by: str
    message: str


class RegistrationResponse(BaseModel):
    """Response for successful agent registration."""
    status: str = "approved"
    agent_id: str
    token: str
    token_expires_at: datetime
    role: str
    access_levels: list[str]
    alias_registered: Optional[str] = None
    message: str


class RegistrationErrorResponse(BaseModel):
    """Response for registration errors."""
    status: str = "error"
    error: str
    message: str
    details: Optional[dict] = None


class RevocationResponse(BaseModel):
    """Response for agent revocation."""
    status: str
    agent_id: str
    aliases_deactivated: list[str] = Field(default_factory=list)
    message: str


class AuditListResponse(BaseModel):
    """Response for audit listing."""
    entries: list[dict]
    total: int
    page: int
    page_size: int


class TokenListResponse(BaseModel):
    """Response for invite token listing."""
    tokens: list[dict]
    total: int
    page: int
    page_size: int
