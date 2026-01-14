"""
DAKB Admin Configuration Schemas

Pydantic v2 models for admin dashboard configuration collections.
These models support dynamic admin management without code changes.

Version: 1.0
Created: 2026-01-14
Author: Claude Opus 4.5

Collections:
- dakb_admin_config: Dynamic admin agent list
- dakb_runtime_config: Runtime configuration settings
- dakb_token_registry: Token tracking and management
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any
import hashlib

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def utcnow() -> datetime:
    """Get current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    """Create SHA256 hash of token for storage (never store raw tokens)."""
    return f"sha256:{hashlib.sha256(token.encode()).hexdigest()}"


# =============================================================================
# ENUMS
# =============================================================================

class ConfigValueType(str, Enum):
    """Types of configuration values."""
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    LIST = "list"
    JSON = "json"


class TokenStatus(str, Enum):
    """Token lifecycle status."""
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    SUSPENDED = "suspended"


# =============================================================================
# ADMIN CONFIG SCHEMAS
# =============================================================================

class AdminConfigBase(BaseModel):
    """Base model for admin configuration."""
    model_config = ConfigDict(populate_by_name=True)


class AdminAgentEntry(BaseModel):
    """Individual admin agent entry with metadata."""
    agent_id: str = Field(..., description="Agent identifier")
    added_at: datetime = Field(default_factory=utcnow)
    added_by: str = Field(..., description="Agent who added this admin")
    notes: Optional[str] = Field(None, max_length=500)


class AdminConfigDocument(AdminConfigBase):
    """
    Document schema for dakb_admin_config collection.

    Stores the dynamic list of admin agents, replacing the hardcoded
    ADMIN_AGENTS frozenset in registration.py.
    """
    config_type: str = Field(default="admin_agents", description="Config type identifier")
    admin_agents: list[AdminAgentEntry] = Field(
        default_factory=list,
        description="List of admin agents with metadata"
    )
    version: int = Field(default=1, description="Config version for concurrency control")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: Optional[str] = Field(None, description="Last agent to update")

    def get_agent_ids(self) -> set[str]:
        """Get set of admin agent IDs."""
        return {entry.agent_id for entry in self.admin_agents}

    def is_admin(self, agent_id: str) -> bool:
        """Check if agent is an admin."""
        return agent_id in self.get_agent_ids()


class AdminConfigCreate(BaseModel):
    """Request model for creating admin config (initial setup)."""
    admin_agents: list[str] = Field(
        ...,
        min_length=1,
        description="Initial list of admin agent IDs"
    )


class AdminAgentAdd(BaseModel):
    """Request model for adding an admin agent."""
    agent_id: str = Field(..., min_length=1, max_length=50)
    notes: Optional[str] = Field(None, max_length=500)


class AdminAgentRemove(BaseModel):
    """Request model for removing an admin agent."""
    agent_id: str = Field(..., min_length=1, max_length=50)


# =============================================================================
# RUNTIME CONFIG SCHEMAS
# =============================================================================

class RuntimeConfigDocument(AdminConfigBase):
    """
    Document schema for dakb_runtime_config collection.

    Stores runtime configuration settings that can be changed
    without restarting the service.
    """
    key: str = Field(..., description="Configuration key (unique)")
    value: Any = Field(..., description="Configuration value")
    value_type: ConfigValueType = Field(..., description="Value type for validation")
    description: str = Field(..., description="Human-readable description")
    default_value: Any = Field(..., description="Default value")
    min_value: Optional[float] = Field(None, description="Minimum value (for numeric)")
    max_value: Optional[float] = Field(None, description="Maximum value (for numeric)")
    allowed_values: Optional[list] = Field(None, description="Allowed values (for enums)")
    category: str = Field(default="general", description="Config category")
    requires_restart: bool = Field(default=False, description="Whether change requires restart")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: Optional[str] = Field(None)

    @field_validator('value', mode='before')
    @classmethod
    def validate_value_type(cls, v, info):
        """Validate value matches declared type."""
        # Validation happens at runtime based on value_type
        return v


class RuntimeConfigUpdate(BaseModel):
    """Request model for updating a runtime config."""
    value: Any = Field(..., description="New configuration value")


class RuntimeConfigCreate(BaseModel):
    """Request model for creating a new runtime config."""
    key: str = Field(..., min_length=1, max_length=50)
    value: Any = Field(...)
    value_type: ConfigValueType = Field(...)
    description: str = Field(..., max_length=500)
    default_value: Any = Field(...)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allowed_values: Optional[list] = None
    category: str = Field(default="general")
    requires_restart: bool = Field(default=False)


# =============================================================================
# TOKEN REGISTRY SCHEMAS
# =============================================================================

class TokenRegistryDocument(AdminConfigBase):
    """
    Document schema for dakb_token_registry collection.

    Tracks token metadata for management (NOT the actual tokens).
    Only stores token fingerprints/hashes for security.
    """
    agent_id: str = Field(..., description="Agent this token belongs to")
    token_fingerprint: str = Field(..., description="SHA256 hash of token")
    status: TokenStatus = Field(default=TokenStatus.ACTIVE)

    # Creation info
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = Field(..., description="Admin who created the token")
    expires_at: datetime = Field(..., description="Token expiration time")

    # Usage tracking
    last_used_at: Optional[datetime] = Field(None)
    use_count: int = Field(default=0)

    # Revocation info
    revoked_at: Optional[datetime] = Field(None)
    revoked_by: Optional[str] = Field(None)
    revocation_reason: Optional[str] = Field(None, max_length=500)

    # Metadata
    agent_type: str = Field(default="claude")
    role: str = Field(default="developer")
    access_levels: list[str] = Field(default_factory=lambda: ["public"])
    notes: Optional[str] = Field(None, max_length=500)

    def is_valid(self) -> bool:
        """Check if token is currently valid."""
        if self.status != TokenStatus.ACTIVE:
            return False
        # Handle timezone-naive datetimes from MongoDB
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= utcnow():
            return False
        return True

    def record_usage(self) -> None:
        """Update usage tracking fields."""
        self.last_used_at = utcnow()
        self.use_count += 1


class TokenRegistryCreate(BaseModel):
    """Request model for registering a new token."""
    agent_id: str = Field(..., min_length=1, max_length=50)
    token_fingerprint: str = Field(..., description="SHA256 hash of the token")
    expires_at: datetime = Field(...)
    agent_type: str = Field(default="claude")
    role: str = Field(default="developer")
    access_levels: list[str] = Field(default_factory=lambda: ["public"])
    notes: Optional[str] = Field(None, max_length=500)


class TokenRefreshRequest(BaseModel):
    """Request model for refreshing a token."""
    extend_hours: int = Field(
        default=8760,  # 365 days
        ge=1,
        le=87600,
        description="Hours to extend token validity"
    )


class TokenRevokeRequest(BaseModel):
    """Request model for revoking a token."""
    reason: Optional[str] = Field(None, max_length=500)


# =============================================================================
# RESPONSE SCHEMAS
# =============================================================================

class AdminAgentListResponse(BaseModel):
    """Response model for listing admin agents."""
    admin_agents: list[AdminAgentEntry]
    total: int
    version: int
    updated_at: datetime


class RuntimeConfigListResponse(BaseModel):
    """Response model for listing runtime configs."""
    configs: list[RuntimeConfigDocument]
    total: int


class TokenRegistryListResponse(BaseModel):
    """Response model for listing tokens."""
    tokens: list[TokenRegistryDocument]
    total: int
    active_count: int
    expired_count: int
    revoked_count: int


class TokenRegistryResponse(BaseModel):
    """Response model for single token info."""
    agent_id: str
    status: TokenStatus
    created_at: datetime
    expires_at: datetime
    last_used_at: Optional[datetime]
    use_count: int
    is_valid: bool
    days_until_expiry: int
    agent_type: str
    role: str
    access_levels: list[str]


# =============================================================================
# DEFAULT RUNTIME CONFIGS
# =============================================================================

DEFAULT_RUNTIME_CONFIGS = [
    {
        "key": "jwt_expiry_hours",
        "value": 8760,
        "value_type": ConfigValueType.INTEGER,
        "description": "JWT token expiry in hours (default 365 days)",
        "default_value": 8760,
        "min_value": 1,
        "max_value": 87600,
        "category": "security",
        "requires_restart": False,
    },
    {
        "key": "rate_limit_requests",
        "value": 100,
        "value_type": ConfigValueType.INTEGER,
        "description": "Maximum requests per rate limit window",
        "default_value": 100,
        "min_value": 1,
        "max_value": 10000,
        "category": "security",
        "requires_restart": False,
    },
    {
        "key": "rate_limit_window",
        "value": 60,
        "value_type": ConfigValueType.INTEGER,
        "description": "Rate limit window in seconds",
        "default_value": 60,
        "min_value": 1,
        "max_value": 3600,
        "category": "security",
        "requires_restart": False,
    },
    {
        "key": "invite_expiry_hours",
        "value": 48,
        "value_type": ConfigValueType.INTEGER,
        "description": "Invite token expiry in hours",
        "default_value": 48,
        "min_value": 1,
        "max_value": 168,
        "category": "registration",
        "requires_restart": False,
    },
    {
        "key": "session_timeout_minutes",
        "value": 60,
        "value_type": ConfigValueType.INTEGER,
        "description": "Admin dashboard session timeout in minutes",
        "default_value": 60,
        "min_value": 5,
        "max_value": 1440,
        "category": "admin",
        "requires_restart": False,
    },
]

# Default admin agents (configurable via dashboard)
DEFAULT_ADMIN_AGENTS = [
    "Coordinator",
    "manager",
    "backend",
    "claude-code-agent",
]
