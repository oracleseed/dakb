"""
DAKB Gateway Registration Routes

REST API routes for agent self-registration with invite-only flow.
Implements Phases 2-5 of the DAKB Self-Registration System v1.0.

Version: 1.3
Created: 2025-12-11
Updated: 2025-12-11
Author: Backend Agent (Claude Opus 4.5)
Session Reference: sess_selfreg_v1_20251211

Changelog:
    v1.3 (2025-12-11):
        - Phase 5: Applied S-Reviewer Phase 4 condition fixes:
          - WARNING-1: Mask target_token in audit log responses
          - WARNING-2: Add self-revocation prevention check
    v1.2 (2025-12-11):
        - Phase 4: Added admin management endpoints
    v1.1 (2025-12-11):
        - Phase 3: Added registration with invite token
    v1.0 (2025-12-11):
        - Phase 2: Initial implementation

Endpoints:
- GET  /api/v1/register/info              - Self-documenting registration schema (public)
- POST /api/v1/register/invite            - Create invite token (admin-only)
- POST /api/v1/register/request           - Register agent with invite token (public)
- DELETE /api/v1/register/revoke/{agent_id} - Revoke agent access (admin-only)
- GET  /api/v1/register/audit             - Audit log with filtering (admin-only)
- GET  /api/v1/register/invites           - List invite tokens (admin-only)

Access Control:
- /info endpoint is public (no auth required) but rate limited
- /invite endpoint requires admin role
- /request endpoint is public but requires valid invite token
- /revoke, /audit, /invites endpoints require admin role
- Rate limiting: 10 invites/hour per admin, 5 registrations/hour per IP

Security Features:
- Cryptographically secure invite token generation (48 bits entropy)
- Atomic token consumption prevents race conditions
- Audit logging for all registration events
- Admin-only invite generation prevents abuse

MAJOR-3 FIX: Sync-in-Async Pattern Documentation
------------------------------------------------
This module uses synchronous PyMongo operations within async FastAPI handlers.
This is intentional and acceptable for the following reasons:

1. **Low Contention**: Registration endpoints have low throughput (invites are
   rate-limited to 10/hour/admin, registrations are one-time events)

2. **Short Operations**: MongoDB operations are quick (<10ms typically) with
   proper indexes, which doesn't significantly block the event loop

3. **Simplicity**: Using async motor driver would add complexity without
   measurable benefit for this use case

4. **Consistency**: The rest of the DAKB service uses PyMongo for simplicity

For high-throughput endpoints, consider using asyncio.to_thread() or motor.
The helper functions store_invite_token() and create_audit_entry() are marked
as async for future-proofing but currently use sync PyMongo underneath.

MAJOR-4 FIX: In-Memory Rate Limiter Limitation
----------------------------------------------
The InviteRateLimiter and RegistrationRateLimiter classes use in-memory
dictionaries to track rate limit state. This has the following limitations:

1. **Single-Process Only**: Rate limits are not shared across multiple worker
   processes. If running multiple uvicorn workers, each has its own state.

2. **No Persistence**: Rate limit state is lost on server restart.

3. **Memory Growth**: Long-lived server instances accumulate IP/agent entries
   (mitigated by automatic cleanup of old timestamps in is_allowed()).

For production deployments with multiple workers, consider:
- Redis-based rate limiting (e.g., using slowapi with redis backend)
- Database-based rate limiting (store timestamps in MongoDB)
- External rate limiting (API gateway, nginx rate limiting)

Current implementation is acceptable for:
- Single-worker deployments
- Development/testing environments
- Low-traffic registration endpoints (which these are by design)

MINOR-2 FIX: Required MongoDB Indexes
-------------------------------------
The following indexes should be created for optimal query performance:

Collection: dakb_invite_tokens
  - { "invite_token": 1 } - unique, for token lookup
  - { "status": 1, "expires_at": 1 } - for active token queries
  - { "created_by": 1, "created_at": -1 } - for admin token listing

Collection: dakb_registration_audit
  - { "timestamp": -1 } - for recent entries
  - { "action": 1, "timestamp": -1 } - for action filtering
  - { "target_agent_id": 1, "timestamp": -1 } - for agent-specific queries
  - { "expires_at": 1 } - TTL index (expireAfterSeconds: 0)

Collection: dakb_agents
  - { "agent_id": 1 } - unique, for agent lookup
  - { "status": 1 } - for active/suspended queries

Collection: dakb_agent_aliases
  - { "alias": 1 } - unique, for alias lookup
  - { "token_id": 1 } - for agent's aliases lookup

To create these indexes, run:
  python backend/dakb_service/scripts/create_registration_indexes.py

PHASE 5 FIXES (S-Reviewer Phase 4 Conditions)
---------------------------------------------
WARNING-1 FIX: Mask target_token in audit log responses
  - Applied mask_token() to target_token in GET /audit endpoint
  - Prevents exposure of full invite tokens in audit log queries
  - Token format displayed: inv_20251211_****6789

WARNING-2 FIX: Self-revocation prevention
  - Added check in DELETE /revoke/{agent_id} endpoint
  - Prevents admins from revoking their own access
  - Returns 400 Bad Request with "self_revocation_forbidden" error
  - Rationale: Prevents accidental lockout, requires another admin
"""

import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..config import get_settings
from ..middleware.auth import (
    AuthenticatedAgent,
    get_current_agent,
    require_role,
    RateLimiter,
    TokenHandler,
    generate_agent_token,
)
from ...db.schemas import AgentRole, AccessLevel, AgentStatus, AgentType as SchemaAgentType
from ...db.registration_schemas.registration import (
    AgentType,
    InviteTokenStatus,
    RegistrationAuditAction,
    DakbInviteToken,
    DakbRegistrationAudit,
    InviteTokenCreate,
    InviteTokenResponse,
    utcnow,
    generate_invite_token,
)
from ...db.collections import get_dakb_client

logger = logging.getLogger(__name__)


# =============================================================================
# RATE LIMITER FOR INVITES (Separate from general rate limiter)
# =============================================================================

class InviteRateLimiter:
    """
    Specialized rate limiter for invite token creation.

    Limits admin agents to 10 invites per hour to prevent abuse.
    Uses a sliding window algorithm.
    """

    def __init__(self, max_invites: int = 10, window_seconds: int = 3600):
        """
        Initialize invite rate limiter.

        Args:
            max_invites: Maximum invites allowed per window (default: 10)
            window_seconds: Time window in seconds (default: 3600 = 1 hour)
        """
        self.max_invites = max_invites
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, agent_id: str) -> bool:
        """
        Check if agent can create another invite.

        Args:
            agent_id: Admin agent identifier.

        Returns:
            True if invite creation is allowed, False if rate limited.
        """
        now = time.time()
        window_start = now - self.window_seconds

        # Get agent's invite timestamps
        timestamps = self._buckets[agent_id]

        # Remove expired timestamps
        timestamps[:] = [ts for ts in timestamps if ts > window_start]

        # Check if under limit
        if len(timestamps) >= self.max_invites:
            return False

        # Add current timestamp
        timestamps.append(now)
        return True

    def get_remaining(self, agent_id: str) -> int:
        """Get remaining invites for agent in current window."""
        now = time.time()
        window_start = now - self.window_seconds

        timestamps = self._buckets.get(agent_id, [])
        active = [ts for ts in timestamps if ts > window_start]

        return max(0, self.max_invites - len(active))

    def get_reset_time(self, agent_id: str) -> Optional[float]:
        """Get seconds until rate limit resets."""
        timestamps = self._buckets.get(agent_id, [])
        if not timestamps:
            return None

        oldest = min(timestamps)
        reset_at = oldest + self.window_seconds
        remaining = reset_at - time.time()

        return max(0, remaining) if remaining > 0 else None


# Global invite rate limiter instance
_invite_rate_limiter: Optional[InviteRateLimiter] = None


def get_invite_rate_limiter() -> InviteRateLimiter:
    """Get or create the global invite rate limiter."""
    global _invite_rate_limiter
    if _invite_rate_limiter is None:
        _invite_rate_limiter = InviteRateLimiter(
            max_invites=10,
            window_seconds=3600  # 1 hour
        )
    return _invite_rate_limiter


# =============================================================================
# ADMIN AGENTS CONFIGURATION
# =============================================================================

# Agents with admin privileges for registration
ADMIN_AGENTS = frozenset({
    "Coordinator",
    "manager",
    "backend",
    "claude-code-agent",
    "claude-code-backend",
    "system",
})


def is_admin_agent(agent_id: str) -> bool:
    """
    Check if agent has admin privileges for registration.

    Args:
        agent_id: Agent identifier to check.

    Returns:
        True if agent is an admin.
    """
    return agent_id in ADMIN_AGENTS


async def require_admin(
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> AuthenticatedAgent:
    """
    Dependency that requires admin role for endpoint access.

    Checks both the agent role and the admin agent list.

    Args:
        agent: Authenticated agent from JWT.

    Returns:
        AuthenticatedAgent if admin.

    Raises:
        HTTPException 403: If agent is not an admin.
    """
    # Check role-based admin
    if agent.role == AgentRole.ADMIN:
        return agent

    # Check agent-based admin
    if is_admin_agent(agent.agent_id):
        return agent

    logger.warning(
        f"Admin access denied for agent '{agent.agent_id}' "
        f"(role: {agent.role.value})"
    )
    raise HTTPException(
        status_code=403,
        detail={
            "error": "admin_required",
            "message": f"Agent '{agent.agent_id}' does not have admin privileges. "
                      "Contact Coordinator or manager for invite tokens."
        }
    )


# =============================================================================
# ROUTER
# =============================================================================

router = APIRouter(
    prefix="/api/v1/register",
    tags=["Registration"],
)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class RegistrationSchemaResponse(BaseModel):
    """Self-documenting registration schema response."""
    service: str = Field(..., description="Service name")
    version: str = Field(..., description="API version")
    mode: str = Field(..., description="Registration mode (invite-only)")
    description: str = Field(..., description="Service description")
    registration_enabled: bool = Field(..., description="Whether registration is enabled")
    invite_required: bool = Field(..., description="Whether invite token is required")
    supported_agent_types: list[str] = Field(..., description="Supported agent types")
    required_fields: list[str] = Field(..., description="Required registration fields")
    optional_fields: list[str] = Field(..., description="Optional registration fields")
    agent_id_pattern: str = Field(..., description="Regex pattern for valid agent_id")
    instructions: dict = Field(..., description="Registration instructions")
    endpoint: str = Field(..., description="Registration endpoint URL")
    method: str = Field(..., description="HTTP method for registration")
    content_type: str = Field(..., description="Content-Type header value")
    request_schema: dict = Field(..., description="Request body schema")
    response_examples: dict = Field(..., description="Example responses")
    example_request: dict = Field(..., description="Example request")


class CreateInviteRequest(BaseModel):
    """Request model for creating invite tokens (simplified for Phase 2)."""
    target_agent_id: Optional[str] = Field(
        None,
        max_length=50,
        description="Optional hint for target agent ID"
    )
    target_agent_type: Optional[str] = Field(
        None,
        description="Optional expected agent type"
    )
    expires_in_hours: int = Field(
        default=48,
        ge=1,
        le=168,
        description="Token validity in hours (1-168, default: 48)"
    )
    max_uses: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Maximum uses for this token (1-10, default: 1)"
    )
    created_by_note: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional note about why this invite was created"
    )


class CreateInviteResponse(BaseModel):
    """Response model for invite token creation."""
    invite_token: str = Field(..., description="Generated invite token")
    expires_at: datetime = Field(..., description="Token expiration time")
    max_uses: int = Field(..., description="Maximum uses allowed")
    message: str = Field(..., description="Status message")


class RegistrationRequest(BaseModel):
    """Request model for agent registration with invite token."""
    agent_id: str = Field(
        ...,
        min_length=4,
        max_length=50,
        description="Unique agent identifier (lowercase, hyphens allowed)"
    )
    agent_type: str = Field(
        ...,
        description="Type of AI agent (claude, gpt, gemini, grok, local, human)"
    )
    invite_token: str = Field(
        ...,
        description="Invite token from admin (format: inv_YYYYMMDD_xxxxxxxxxxxx)"
    )
    display_name: Optional[str] = Field(
        None,
        max_length=100,
        description="Human-readable display name"
    )
    alias: Optional[str] = Field(
        None,
        max_length=50,
        description="Optional alias to register"
    )
    alias_role: Optional[str] = Field(
        None,
        max_length=100,
        description="Role for the alias"
    )
    callback_url: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional callback URL for notifications"
    )
    machine_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Machine identifier"
    )
    capabilities: Optional[list[str]] = Field(
        None,
        description="Agent capabilities"
    )
    model_version: Optional[str] = Field(
        None,
        max_length=50,
        description="LLM model version"
    )

    @field_validator('agent_id')
    @classmethod
    def validate_agent_id_format(cls, v: str) -> str:
        """Validate agent_id follows naming convention."""
        pattern = r'^[a-z0-9][a-z0-9-]{2,48}[a-z0-9]$'
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid agent_id format. Must be lowercase alphanumeric with hyphens, "
                f"4-50 chars, start/end with alphanumeric. Got: {v}"
            )
        return v

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

    @field_validator('agent_type')
    @classmethod
    def validate_agent_type(cls, v: str) -> str:
        """Validate agent type is supported."""
        valid_types = {'claude', 'claude_code', 'gpt', 'openai', 'gemini', 'grok', 'local', 'human'}
        if v.lower() not in valid_types:
            raise ValueError(
                f"Invalid agent_type. Must be one of: {', '.join(sorted(valid_types))}. Got: {v}"
            )
        return v.lower()


class RegistrationResponse(BaseModel):
    """Response model for successful agent registration."""
    status: str = Field(default="approved", description="Registration status")
    agent_id: str = Field(..., description="Registered agent ID")
    token: str = Field(..., description="Full authentication token")
    expires_at: datetime = Field(..., description="Token expiration time")
    role: str = Field(..., description="Assigned role")
    access_levels: list[str] = Field(..., description="Granted access levels")
    alias_registered: Optional[str] = Field(None, description="Registered alias if provided")
    message: str = Field(..., description="Status message")
    # Documentation links for new agents
    documentation: dict = Field(
        default_factory=lambda: {
            "swagger_ui": "/docs",
            "redoc": "/redoc",
            "openapi_spec": "/openapi.json",
            "quick_start": "Use 'Authorization: Bearer <token>' header for all API requests"
        },
        description="Links to API documentation for learning the system"
    )


# =============================================================================
# PHASE 4 RESPONSE MODELS
# =============================================================================

class RevocationResponse(BaseModel):
    """Response model for agent revocation."""
    status: str = Field(default="revoked", description="Revocation status")
    agent_id: str = Field(..., description="Revoked agent ID")
    revoked_at: datetime = Field(..., description="Revocation timestamp")
    revoked_by: str = Field(..., description="Admin who performed revocation")
    aliases_deactivated: list[str] = Field(
        default_factory=list,
        description="List of aliases that were deactivated"
    )
    message: str = Field(..., description="Status message")


class AuditEntryResponse(BaseModel):
    """Response model for a single audit entry."""
    audit_id: str = Field(..., description="Unique audit entry ID")
    timestamp: datetime = Field(..., description="Event timestamp")
    action: str = Field(..., description="Action type")
    actor_agent_id: str = Field(..., description="Agent that performed the action")
    actor_ip: Optional[str] = Field(None, description="IP address of actor")
    target_token: Optional[str] = Field(None, description="Related invite token")
    target_agent_id: Optional[str] = Field(None, description="Affected agent ID")
    details: dict = Field(default_factory=dict, description="Additional details")
    success: bool = Field(..., description="Whether action succeeded")
    error_message: Optional[str] = Field(None, description="Error message if failed")


class AuditListResponse(BaseModel):
    """Response model for audit log listing."""
    entries: list[AuditEntryResponse] = Field(..., description="Audit entries")
    total: int = Field(..., description="Total matching entries")
    skip: int = Field(..., description="Number of entries skipped")
    limit: int = Field(..., description="Maximum entries returned")


class InviteTokenListItem(BaseModel):
    """Response model for a single invite token in listing."""
    invite_token: str = Field(..., description="Token identifier (masked)")
    created_by: str = Field(..., description="Admin who created the token")
    created_at: datetime = Field(..., description="Creation timestamp")
    expires_at: datetime = Field(..., description="Expiration timestamp")
    status: str = Field(..., description="Token status")
    for_agent_type: Optional[str] = Field(None, description="Expected agent type")
    for_agent_id_hint: Optional[str] = Field(None, description="Suggested agent ID")
    purpose: str = Field(..., description="Purpose description")
    used_by_agent_id: Optional[str] = Field(None, description="Agent that used token")
    used_at: Optional[datetime] = Field(None, description="Usage timestamp")


class InviteListResponse(BaseModel):
    """Response model for invite token listing."""
    tokens: list[InviteTokenListItem] = Field(..., description="Invite tokens")
    total: int = Field(..., description="Total matching tokens")
    skip: int = Field(..., description="Number of tokens skipped")
    limit: int = Field(..., description="Maximum tokens returned")


# =============================================================================
# REGISTRATION RATE LIMITER
# =============================================================================

class RegistrationRateLimiter:
    """
    Rate limiter for registration attempts.

    Limits to 5 registration attempts per hour per IP address
    to prevent abuse while still allowing legitimate registrations.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 3600):
        """
        Initialize registration rate limiter.

        Args:
            max_attempts: Maximum attempts allowed per window (default: 5)
            window_seconds: Time window in seconds (default: 3600 = 1 hour)
        """
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, ip_address: str) -> bool:
        """Check if registration attempt is allowed."""
        now = time.time()
        window_start = now - self.window_seconds

        timestamps = self._buckets[ip_address]
        timestamps[:] = [ts for ts in timestamps if ts > window_start]

        if len(timestamps) >= self.max_attempts:
            return False

        timestamps.append(now)
        return True

    def get_remaining(self, ip_address: str) -> int:
        """Get remaining attempts for IP."""
        now = time.time()
        window_start = now - self.window_seconds
        timestamps = self._buckets.get(ip_address, [])
        active = [ts for ts in timestamps if ts > window_start]
        return max(0, self.max_attempts - len(active))

    def get_reset_time(self, ip_address: str) -> Optional[float]:
        """Get seconds until rate limit resets."""
        timestamps = self._buckets.get(ip_address, [])
        if not timestamps:
            return None
        oldest = min(timestamps)
        reset_at = oldest + self.window_seconds
        remaining = reset_at - time.time()
        return max(0, remaining) if remaining > 0 else None


# Global registration rate limiter instance
_registration_rate_limiter: Optional[RegistrationRateLimiter] = None


def get_registration_rate_limiter() -> RegistrationRateLimiter:
    """Get or create the global registration rate limiter."""
    global _registration_rate_limiter
    if _registration_rate_limiter is None:
        _registration_rate_limiter = RegistrationRateLimiter(
            max_attempts=5,
            window_seconds=3600  # 1 hour
        )
    return _registration_rate_limiter


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_db():
    """Get database connection."""
    settings = get_settings()
    client = get_dakb_client()
    return client[settings.db_name]


def _sync_store_invite_token(token_doc: dict) -> bool:
    """Synchronous helper for storing invite token."""
    db = get_db()
    try:
        result = db.dakb_invite_tokens.insert_one(token_doc)
        return result.inserted_id is not None
    except Exception as e:
        logger.error(f"Failed to store invite token: {e}")
        return False


async def store_invite_token(token_doc: dict) -> bool:
    """
    Store invite token in MongoDB (async wrapper).

    MAJOR-3 FIX: Uses asyncio.to_thread() for proper async execution.

    Args:
        token_doc: Token document to store.

    Returns:
        True if stored successfully.
    """
    return await asyncio.to_thread(_sync_store_invite_token, token_doc)


def _sync_create_audit_entry(audit_doc: dict) -> bool:
    """Synchronous helper for creating audit entry."""
    db = get_db()
    try:
        result = db.dakb_registration_audit.insert_one(audit_doc)
        return result.inserted_id is not None
    except Exception as e:
        logger.error(f"Failed to create audit entry: {e}")
        return False


async def create_audit_entry(audit_doc: dict) -> bool:
    """
    Create registration audit entry (async wrapper).

    MAJOR-3 FIX: Uses asyncio.to_thread() for proper async execution.

    Args:
        audit_doc: Audit document to store.

    Returns:
        True if stored successfully.
    """
    return await asyncio.to_thread(_sync_create_audit_entry, audit_doc)


def _sync_validate_and_consume_token(invite_token: str, agent_id: str) -> tuple[Optional[dict], Optional[str]]:
    """
    Synchronous atomic token validation and consumption.

    Uses MongoDB findOneAndUpdate for atomic operation to prevent race conditions.

    Args:
        invite_token: Token to validate and consume.
        agent_id: Agent ID attempting to use the token.

    Returns:
        Tuple of (token_doc, error_message).
        On success: (token_doc, None)
        On failure: (None, error_message)
    """
    db = get_db()
    now = utcnow()

    try:
        # Atomic update: only succeeds if token is ACTIVE and not expired
        result = db.dakb_invite_tokens.find_one_and_update(
            {
                "invite_token": invite_token,
                "status": InviteTokenStatus.ACTIVE.value,
                "expires_at": {"$gt": now}
            },
            {
                "$set": {
                    "status": InviteTokenStatus.USED.value,
                    "used_by_agent_id": agent_id,
                    "used_at": now
                }
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(f"Invite token consumed: {invite_token[:20]}... by {agent_id}")
            return result, None

        # Token not found, expired, or already used - determine why
        token_doc = db.dakb_invite_tokens.find_one({"invite_token": invite_token})

        if token_doc is None:
            return None, "invalid_token"

        status = token_doc.get("status")
        if status == InviteTokenStatus.USED.value:
            used_by = token_doc.get("used_by_agent_id", "unknown")
            return None, f"token_already_used:{used_by}"

        if status == InviteTokenStatus.REVOKED.value:
            return None, "token_revoked"

        expires_at = token_doc.get("expires_at")
        if expires_at and expires_at <= now:
            return None, f"token_expired:{expires_at.isoformat()}"

        return None, f"token_invalid_status:{status}"

    except Exception as e:
        logger.error(f"Error validating invite token: {e}")
        return None, f"internal_error:{str(e)}"


async def validate_and_consume_token(invite_token: str, agent_id: str) -> tuple[Optional[dict], Optional[str]]:
    """
    Atomically validate and consume an invite token (async wrapper).

    Args:
        invite_token: Token to validate.
        agent_id: Agent attempting to use the token.

    Returns:
        Tuple of (token_doc, error_message).
    """
    return await asyncio.to_thread(_sync_validate_and_consume_token, invite_token, agent_id)


def _sync_rollback_token_consumption(invite_token: str) -> bool:
    """Synchronous helper for rolling back token consumption."""
    db = get_db()
    try:
        result = db.dakb_invite_tokens.update_one(
            {
                "invite_token": invite_token,
                "status": InviteTokenStatus.USED.value
            },
            {
                "$set": {
                    "status": InviteTokenStatus.ACTIVE.value,
                    "used_by_agent_id": None,
                    "used_at": None
                }
            }
        )
        if result.modified_count > 0:
            logger.info(f"Token consumption rolled back: {invite_token[:20]}...")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to rollback token: {e}")
        return False


async def rollback_token_consumption(invite_token: str) -> bool:
    """
    Rollback a token consumption (async wrapper).

    Used when registration fails after token was consumed.

    Args:
        invite_token: Token to rollback.

    Returns:
        True if rollback successful.
    """
    return await asyncio.to_thread(_sync_rollback_token_consumption, invite_token)


def _sync_check_agent_exists(agent_id: str) -> bool:
    """Check if agent ID already exists in database."""
    db = get_db()
    try:
        result = db.dakb_agents.find_one({"agent_id": agent_id})
        return result is not None
    except Exception as e:
        logger.error(f"Error checking agent existence: {e}")
        return False


async def check_agent_exists(agent_id: str) -> bool:
    """Check if agent ID already exists (async wrapper)."""
    return await asyncio.to_thread(_sync_check_agent_exists, agent_id)


def _sync_check_alias_available(alias: str) -> bool:
    """Check if alias is available for registration."""
    db = get_db()
    try:
        result = db.dakb_agent_aliases.find_one({"alias": alias})
        return result is None
    except Exception as e:
        logger.error(f"Error checking alias availability: {e}")
        return False


async def check_alias_available(alias: str) -> bool:
    """Check if alias is available (async wrapper)."""
    return await asyncio.to_thread(_sync_check_alias_available, alias)


def _sync_create_agent(agent_doc: dict) -> bool:
    """Create agent in database."""
    db = get_db()
    try:
        result = db.dakb_agents.insert_one(agent_doc)
        return result.inserted_id is not None
    except Exception as e:
        logger.error(f"Failed to create agent: {e}")
        return False


async def create_agent(agent_doc: dict) -> bool:
    """Create agent in database (async wrapper)."""
    return await asyncio.to_thread(_sync_create_agent, agent_doc)


def _sync_create_alias(alias_doc: dict) -> bool:
    """Create agent alias in database."""
    db = get_db()
    try:
        result = db.dakb_agent_aliases.insert_one(alias_doc)
        return result.inserted_id is not None
    except Exception as e:
        logger.error(f"Failed to create alias: {e}")
        return False


async def create_alias(alias_doc: dict) -> bool:
    """Create agent alias in database (async wrapper)."""
    return await asyncio.to_thread(_sync_create_alias, alias_doc)


# =============================================================================
# PHASE 4 HELPER FUNCTIONS
# =============================================================================

def _sync_get_agent(agent_id: str) -> Optional[dict]:
    """Get agent document from database."""
    db = get_db()
    try:
        result = db.dakb_agents.find_one({"agent_id": agent_id})
        if result:
            result.pop("_id", None)
        return result
    except Exception as e:
        logger.error(f"Error getting agent '{agent_id}': {e}")
        return None


async def get_agent(agent_id: str) -> Optional[dict]:
    """Get agent document (async wrapper)."""
    return await asyncio.to_thread(_sync_get_agent, agent_id)


def _sync_revoke_agent(agent_id: str) -> bool:
    """
    Revoke agent access by marking as suspended.

    Args:
        agent_id: Agent to revoke.

    Returns:
        True if agent was revoked successfully.
    """
    db = get_db()
    now = utcnow()
    try:
        result = db.dakb_agents.update_one(
            {"agent_id": agent_id, "status": {"$ne": "suspended"}},
            {
                "$set": {
                    "status": "suspended",
                    "updated_at": now,
                    "last_activity": "Account suspended by admin"
                }
            }
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Failed to revoke agent '{agent_id}': {e}")
        return False


async def revoke_agent(agent_id: str) -> bool:
    """Revoke agent access (async wrapper)."""
    return await asyncio.to_thread(_sync_revoke_agent, agent_id)


def _sync_deactivate_agent_aliases(agent_id: str) -> list[str]:
    """
    Deactivate all aliases for an agent.

    Args:
        agent_id: Agent whose aliases should be deactivated.

    Returns:
        List of deactivated alias names.
    """
    db = get_db()
    deactivated = []
    try:
        # Find all active aliases for this agent
        aliases = list(db.dakb_agent_aliases.find({
            "token_id": agent_id,
            "is_active": True
        }))

        for alias_doc in aliases:
            db.dakb_agent_aliases.update_one(
                {"_id": alias_doc["_id"]},
                {"$set": {"is_active": False}}
            )
            deactivated.append(alias_doc.get("alias", "unknown"))

        return deactivated
    except Exception as e:
        logger.error(f"Failed to deactivate aliases for '{agent_id}': {e}")
        return deactivated


async def deactivate_agent_aliases(agent_id: str) -> list[str]:
    """Deactivate all aliases for an agent (async wrapper)."""
    return await asyncio.to_thread(_sync_deactivate_agent_aliases, agent_id)


def _sync_get_audit_entries(
    agent_id: Optional[str] = None,
    action: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 50
) -> tuple[list[dict], int]:
    """
    Get audit log entries with filtering.

    Args:
        agent_id: Filter by target agent ID.
        action: Filter by action type.
        start_date: Filter entries after this date.
        end_date: Filter entries before this date.
        skip: Number of entries to skip (pagination).
        limit: Maximum entries to return.

    Returns:
        Tuple of (entries_list, total_count).
    """
    db = get_db()
    try:
        # Build query filter
        query = {}

        if agent_id:
            query["target_agent_id"] = agent_id

        if action:
            query["action"] = action

        if start_date or end_date:
            query["timestamp"] = {}
            if start_date:
                query["timestamp"]["$gte"] = start_date
            if end_date:
                query["timestamp"]["$lte"] = end_date

        # Get total count
        total = db.dakb_registration_audit.count_documents(query)

        # Get entries with pagination
        cursor = db.dakb_registration_audit.find(query).sort(
            "timestamp", -1
        ).skip(skip).limit(limit)

        entries = []
        for doc in cursor:
            doc.pop("_id", None)
            entries.append(doc)

        return entries, total
    except Exception as e:
        logger.error(f"Failed to get audit entries: {e}")
        return [], 0


async def get_audit_entries(
    agent_id: Optional[str] = None,
    action: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 50
) -> tuple[list[dict], int]:
    """Get audit log entries with filtering (async wrapper)."""
    return await asyncio.to_thread(
        _sync_get_audit_entries,
        agent_id, action, start_date, end_date, skip, limit
    )


def _sync_get_invite_tokens(
    status: Optional[str] = None,
    created_by: Optional[str] = None,
    skip: int = 0,
    limit: int = 20
) -> tuple[list[dict], int]:
    """
    Get invite tokens with filtering.

    Args:
        status: Filter by token status (active, used, expired, revoked).
        created_by: Filter by admin who created the token.
        skip: Number of tokens to skip (pagination).
        limit: Maximum tokens to return.

    Returns:
        Tuple of (tokens_list, total_count).
    """
    db = get_db()
    try:
        # Build query filter
        query = {}

        if status:
            query["status"] = status

        if created_by:
            query["created_by"] = created_by

        # Get total count
        total = db.dakb_invite_tokens.count_documents(query)

        # Get tokens with pagination
        cursor = db.dakb_invite_tokens.find(query).sort(
            "created_at", -1
        ).skip(skip).limit(limit)

        tokens = []
        for doc in cursor:
            doc.pop("_id", None)
            tokens.append(doc)

        return tokens, total
    except Exception as e:
        logger.error(f"Failed to get invite tokens: {e}")
        return [], 0


async def get_invite_tokens(
    status: Optional[str] = None,
    created_by: Optional[str] = None,
    skip: int = 0,
    limit: int = 20
) -> tuple[list[dict], int]:
    """Get invite tokens with filtering (async wrapper)."""
    return await asyncio.to_thread(
        _sync_get_invite_tokens,
        status, created_by, skip, limit
    )


def mask_token(token: str) -> str:
    """
    Mask an invite token for display.

    Shows first 12 chars and last 4 chars: inv_20251211_****1234
    """
    if len(token) <= 16:
        return token[:8] + "****"
    return token[:16] + "****" + token[-4:]


# =============================================================================
# PUBLIC ENDPOINTS
# =============================================================================

@router.get(
    "/info",
    response_model=RegistrationSchemaResponse,
    summary="Get registration info",
    description="""
Self-documenting registration schema endpoint.

Returns complete instructions and schema for AI agents to register with DAKB.
This endpoint is **public** (no authentication required) but registration
itself requires a valid invite token from an admin.

**Use Case:**
AI agents can fetch this schema to understand how to register programmatically.
    """
)
async def get_registration_info() -> RegistrationSchemaResponse:
    """
    Self-documenting registration schema endpoint.

    Returns complete instructions and schema for AI agents to register.
    This endpoint is public but registration requires an invite token.

    Returns:
        RegistrationSchemaResponse with full schema and instructions.
    """
    return RegistrationSchemaResponse(
        service="DAKB (Distributed Agent Knowledge Base)",
        version="1.0",
        mode="invite-only",
        description="Agent registration system. Registration requires a valid invite token from an admin.",
        registration_enabled=True,
        invite_required=True,
        supported_agent_types=["claude", "claude_code", "gpt", "openai", "gemini", "grok", "local", "human"],
        required_fields=["agent_id", "agent_type", "invite_token"],
        optional_fields=["display_name", "alias", "alias_role", "callback_url", "model_version", "capabilities"],
        agent_id_pattern="^[a-z0-9][a-z0-9-]{2,48}[a-z0-9]$",

        instructions={
            "overview": "To register with DAKB, you need an invite token from an admin. "
                       "Send a POST request to the endpoint URL with the invite token and your agent details.",
            "steps": [
                "1. Obtain an invite token from a DAKB admin (Coordinator, manager, or backend agent)",
                "2. Review the request_schema below to understand required fields",
                "3. POST to the endpoint URL with Content-Type: application/json",
                "4. On success, you'll receive a full access token immediately",
                "5. Use the token in Authorization header: Bearer <token>"
            ],
            "tips": [
                "Choose a unique agent_id (lowercase, hyphens allowed, 4-50 chars)",
                "Provide a clear purpose description",
                "If invite includes pre-registered alias, it will be auto-activated",
                "The agent_id pattern: start/end with alphanumeric, hyphens in middle only"
            ],
            "admin_contact": "Request invite token from Coordinator or manager agent via DAKB messaging",
            "rate_limits": {
                "registration_attempts": "5 per hour per IP",
                "invite_generation": "10 per hour per admin"
            }
        },

        endpoint="/api/v1/register/request",
        method="POST",
        content_type="application/json",

        request_schema={
            "invite_token": {
                "type": "string",
                "required": True,
                "description": "Invite token provided by admin. Format: inv_YYYYMMDD_xxxxx",
                "pattern": "^inv_[0-9]{8}_[a-f0-9]{12}$"
            },
            "agent_id": {
                "type": "string",
                "required": True,
                "description": "Unique identifier for your agent. Lowercase with hyphens.",
                "pattern": "^[a-z0-9][a-z0-9-]{2,48}[a-z0-9]$",
                "examples": ["gemini-research-v1", "gpt-code-assistant"]
            },
            "agent_type": {
                "type": "string",
                "required": True,
                "enum": ["claude", "claude_code", "gpt", "openai", "gemini", "grok", "local", "human"],
                "description": "Type of AI agent"
            },
            "display_name": {
                "type": "string",
                "required": False,
                "description": "Human-readable display name",
                "max_length": 100
            },
            "machine_id": {
                "type": "string",
                "required": True,
                "description": "Identifier for the machine running this agent"
            },
            "purpose": {
                "type": "string",
                "required": True,
                "description": "What this agent does and why it needs DAKB access",
                "min_length": 20,
                "max_length": 500
            },
            "alias": {
                "type": "string",
                "required": False,
                "description": "Optional alias to register (e.g., 'Coordinator', 'Reviewer')",
                "max_length": 50
            },
            "alias_role": {
                "type": "string",
                "required": False,
                "description": "Role for the alias (e.g., 'orchestration', 'code_review')",
                "max_length": 100
            },
            "capabilities": {
                "type": "array",
                "required": False,
                "description": "Agent capabilities (coding, debugging, research, etc.)",
                "items": {"type": "string"}
            },
            "model_version": {
                "type": "string",
                "required": False,
                "description": "LLM model version (e.g., 'claude-opus-4-5', 'gpt-5.1', 'gemini-3-pro')"
            }
        },

        response_examples={
            "success": {
                "status": "approved",
                "agent_id": "gemini-research-v1",
                "token": "eyJ...(full access token)",
                "token_expires_at": "2026-01-10T12:00:00Z",
                "role": "developer",
                "access_levels": ["public"],
                "alias_registered": "ResearchAssistant",
                "message": "Registration successful. Use token in Authorization header."
            },
            "error_invalid_token": {
                "status": "error",
                "error": "invalid_token",
                "message": "Invite token is invalid, expired, or already used."
            },
            "error_agent_exists": {
                "status": "error",
                "error": "agent_id_taken",
                "message": "Agent ID 'my-agent' is already registered."
            },
            "error_token_expired": {
                "status": "error",
                "error": "token_expired",
                "message": "Invite token has expired. Request a new invite from an admin."
            }
        },

        example_request={
            "curl": 'curl -X POST https://localhost:3100/api/v1/register/request '
                   '-H "Content-Type: application/json" '
                   '-d \'{"invite_token": "inv_20251211_abc123456789", '
                   '"agent_id": "gemini-research-v1", "agent_type": "gemini", '
                   '"display_name": "Gemini Research", "machine_id": "gcp-1", '
                   '"purpose": "Research assistant for trading analysis"}\'',
            "json_body": {
                "invite_token": "inv_20251211_abc123456789",
                "agent_id": "gemini-research-v1",
                "agent_type": "gemini",
                "display_name": "Gemini Research Assistant",
                "machine_id": "gcp-instance-1",
                "purpose": "Research assistant for analyzing trading strategies and market data"
            }
        }
    )


# =============================================================================
# ADMIN-ONLY ENDPOINTS
# =============================================================================

@router.post(
    "/invite",
    response_model=CreateInviteResponse,
    summary="Create invite token",
    description="""
Generate an invite token for a new agent to register with DAKB.

**Admin-only endpoint.** Rate limited to 10 invites per hour per admin.

The invite token should be shared with the intended agent who will use it
to complete registration at the `/api/v1/register/request` endpoint.

**Token Properties:**
- Single-use by default (max_uses=1)
- Expires after specified hours (default: 48)
- Cryptographically secure (48 bits entropy)
- Format: inv_YYYYMMDD_xxxxxxxxxxxx
    """,
    # MAJOR-2 FIX: Removed duplicate dependencies=[Depends(require_admin)]
    # The require_admin dependency is already declared in the function signature
)
async def create_invite_token(
    request: CreateInviteRequest,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> CreateInviteResponse:
    """
    Generate an invite token for a new agent.

    Admin-only endpoint. Rate limited to 10 invites/hour per admin.

    Args:
        request: Invite token configuration.
        admin: Authenticated admin agent.

    Returns:
        CreateInviteResponse with the generated token.

    Raises:
        HTTPException 429: If rate limit exceeded.
        HTTPException 500: If token creation fails.
    """
    admin_id = admin.agent_id

    # Check rate limit for invites
    rate_limiter = get_invite_rate_limiter()
    if not rate_limiter.is_allowed(admin_id):
        reset_time = rate_limiter.get_reset_time(admin_id)
        remaining = rate_limiter.get_remaining(admin_id)

        logger.warning(f"Invite rate limit exceeded for admin '{admin_id}'")
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Invite rate limit exceeded. You have created 10 invites in the past hour.",
                "retry_after_seconds": int(reset_time) if reset_time else 3600,
                "remaining_invites": remaining
            },
            headers={
                "Retry-After": str(int(reset_time)) if reset_time else "3600",
                "X-RateLimit-Remaining": str(remaining),
            }
        )

    # Generate cryptographically secure token
    invite_token = generate_invite_token()

    # Calculate expiry
    now = utcnow()
    expires_at = now + timedelta(hours=request.expires_in_hours)

    # Build purpose from request or default
    purpose = request.created_by_note or f"Invite created by {admin_id}"
    if request.target_agent_id:
        purpose = f"{purpose} (for {request.target_agent_id})"

    # Parse target agent type if provided
    target_agent_type = None
    if request.target_agent_type:
        try:
            target_agent_type = AgentType(request.target_agent_type)
        except ValueError:
            logger.warning(f"Unknown target agent type: {request.target_agent_type}")

    # Create token document
    token_doc = DakbInviteToken(
        invite_token=invite_token,
        created_by=admin_id,
        created_at=now,
        for_agent_type=target_agent_type,
        for_agent_id_hint=request.target_agent_id,
        purpose=purpose if len(purpose) >= 10 else f"Invite for new agent registration ({purpose})",
        granted_role=AgentRole.DEVELOPER,
        granted_access_levels=[AccessLevel.PUBLIC],
        status=InviteTokenStatus.ACTIVE,
        expires_at=expires_at,
        admin_notes=request.created_by_note,
    )

    # Store token in database
    token_dict = token_doc.model_dump()
    # Convert enums to strings for MongoDB
    token_dict["granted_role"] = token_doc.granted_role.value
    token_dict["granted_access_levels"] = [al.value for al in token_doc.granted_access_levels]
    token_dict["status"] = token_doc.status.value
    if token_dict.get("for_agent_type"):
        token_dict["for_agent_type"] = token_doc.for_agent_type.value

    stored = await store_invite_token(token_dict)
    if not stored:
        logger.error(f"Failed to store invite token created by '{admin_id}'")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "storage_error",
                "message": "Failed to store invite token. Please try again."
            }
        )

    # Create audit entry
    audit_doc = DakbRegistrationAudit(
        action=RegistrationAuditAction.INVITE_CREATED,
        actor_agent_id=admin_id,
        target_token=invite_token,
        target_agent_id=request.target_agent_id,
        details={
            "for_agent_type": str(target_agent_type.value) if target_agent_type else None,
            "for_agent_id_hint": request.target_agent_id,
            "expires_at": expires_at.isoformat(),
            "expires_in_hours": request.expires_in_hours,
            "max_uses": request.max_uses,
        },
        success=True
    )

    await create_audit_entry(audit_doc.model_dump())

    logger.info(
        f"Invite token created by admin '{admin_id}': {invite_token[:20]}... "
        f"(expires: {expires_at.isoformat()}, max_uses: {request.max_uses})"
    )

    return CreateInviteResponse(
        invite_token=invite_token,
        expires_at=expires_at,
        max_uses=request.max_uses,
        message=f"Invite token created. Valid for {request.expires_in_hours} hours. "
               f"Share this token with the new agent to complete registration."
    )


# =============================================================================
# PUBLIC REGISTRATION ENDPOINT
# =============================================================================

@router.post(
    "/request",
    response_model=RegistrationResponse,
    status_code=201,  # MINOR-4 FIX: Return 201 Created for successful registration
    summary="Register agent with invite token",
    description="""
Register a new agent with DAKB using a valid invite token.

**Public endpoint** - no authentication required, but requires a valid invite token.
Rate limited to 5 attempts per hour per IP address.

**Registration Process:**
1. Validate invite token (atomic - prevents race conditions)
2. Check agent_id uniqueness
3. Check alias availability (if provided)
4. Create agent in database
5. Register alias (if provided)
6. Generate authentication token
7. Return token for immediate use

**Token Consumption:**
The invite token is atomically consumed when registration begins.
If registration fails after token consumption, the token is rolled back.
    """,
    responses={
        201: {
            "description": "Registration successful",
            "model": RegistrationResponse
        },
        400: {
            "description": "Invalid invite token",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "error": "invalid_token",
                            "message": "Invite token is invalid or does not exist."
                        }
                    }
                }
            }
        },
        409: {
            "description": "Agent ID already exists",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "error": "agent_id_taken",
                            "message": "Agent ID 'my-agent' is already registered."
                        }
                    }
                }
            }
        },
        422: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "error": "validation_error",
                            "message": "Invalid agent_id format."
                        }
                    }
                }
            }
        },
        429: {
            "description": "Rate limit exceeded",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "error": "rate_limit_exceeded",
                            "message": "Too many registration attempts. Try again in 30 minutes."
                        }
                    }
                }
            }
        }
    }
)
async def register_agent(
    request_body: RegistrationRequest,
    request: Request,
) -> RegistrationResponse:
    """
    Register a new agent with DAKB using an invite token.

    This endpoint atomically:
    1. Validates and consumes the invite token
    2. Creates the agent in dakb_agents collection
    3. Registers optional alias if provided
    4. Generates and returns authentication token

    Args:
        request_body: Registration request with invite token and agent details.
        request: FastAPI request object (for IP address).

    Returns:
        RegistrationResponse with authentication token on success.

    Raises:
        HTTPException 400: Invalid invite token
        HTTPException 409: Agent ID already exists
        HTTPException 422: Validation error
        HTTPException 429: Rate limit exceeded
        HTTPException 500: Internal server error
    """
    # Get client IP for rate limiting
    client_ip = request.client.host if request.client else "unknown"

    # Check registration rate limit
    rate_limiter = get_registration_rate_limiter()
    if not rate_limiter.is_allowed(client_ip):
        reset_time = rate_limiter.get_reset_time(client_ip)
        remaining = rate_limiter.get_remaining(client_ip)

        logger.warning(f"Registration rate limit exceeded for IP: {client_ip}")

        # Create audit entry for rate-limited attempt
        audit_doc = DakbRegistrationAudit(
            action=RegistrationAuditAction.REGISTRATION_FAILED,
            actor_agent_id=request_body.agent_id,
            actor_ip=client_ip,
            target_token=request_body.invite_token,
            target_agent_id=request_body.agent_id,
            details={"reason": "rate_limit_exceeded"},
            success=False,
            error_message="Registration rate limit exceeded"
        )
        await create_audit_entry(audit_doc.model_dump())

        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Too many registration attempts. Try again in {int(reset_time / 60) if reset_time else 60} minutes.",
                "retry_after_seconds": int(reset_time) if reset_time else 3600,
                "remaining_attempts": remaining
            },
            headers={
                "Retry-After": str(int(reset_time)) if reset_time else "3600",
                "X-RateLimit-Remaining": str(remaining),
            }
        )

    # Step 1: Check if agent_id already exists (before consuming token)
    agent_exists = await check_agent_exists(request_body.agent_id)
    if agent_exists:
        logger.warning(f"Registration failed: Agent ID '{request_body.agent_id}' already exists")

        # Create audit entry for duplicate agent attempt
        audit_doc = DakbRegistrationAudit(
            action=RegistrationAuditAction.REGISTRATION_FAILED,
            actor_agent_id=request_body.agent_id,
            actor_ip=client_ip,
            target_token=request_body.invite_token,
            target_agent_id=request_body.agent_id,
            details={"reason": "agent_id_taken"},
            success=False,
            error_message=f"Agent ID '{request_body.agent_id}' is already registered"
        )
        await create_audit_entry(audit_doc.model_dump())

        raise HTTPException(
            status_code=409,
            detail={
                "error": "agent_id_taken",
                "message": f"Agent ID '{request_body.agent_id}' is already registered. "
                          "Please choose a different agent_id."
            }
        )

    # Step 2: Check alias availability if provided (before consuming token)
    alias_to_register = request_body.alias
    if alias_to_register:
        alias_available = await check_alias_available(alias_to_register)
        if not alias_available:
            logger.warning(f"Registration failed: Alias '{alias_to_register}' already taken")

            # Create audit entry for duplicate alias attempt
            audit_doc = DakbRegistrationAudit(
                action=RegistrationAuditAction.REGISTRATION_FAILED,
                actor_agent_id=request_body.agent_id,
                actor_ip=client_ip,
                target_token=request_body.invite_token,
                target_agent_id=request_body.agent_id,
                details={"reason": "alias_taken", "alias": alias_to_register},
                success=False,
                error_message=f"Alias '{alias_to_register}' is already taken"
            )
            await create_audit_entry(audit_doc.model_dump())

            raise HTTPException(
                status_code=409,
                detail={
                    "error": "alias_taken",
                    "message": f"Alias '{alias_to_register}' is already taken. "
                              "Please choose a different alias or omit it."
                }
            )

    # Step 3: Atomically validate and consume invite token
    token_doc, error = await validate_and_consume_token(
        request_body.invite_token,
        request_body.agent_id
    )

    if error:
        # Parse error type for appropriate response
        error_type = error.split(":")[0] if ":" in error else error
        error_detail = error.split(":", 1)[1] if ":" in error else ""

        logger.warning(
            f"Registration failed for '{request_body.agent_id}': Token error - {error}"
        )

        # Create audit entry for token error
        audit_doc = DakbRegistrationAudit(
            action=RegistrationAuditAction.REGISTRATION_FAILED,
            actor_agent_id=request_body.agent_id,
            actor_ip=client_ip,
            target_token=request_body.invite_token,
            target_agent_id=request_body.agent_id,
            details={"reason": error_type, "detail": error_detail},
            success=False,
            error_message=error
        )
        await create_audit_entry(audit_doc.model_dump())

        # Map error types to HTTP responses
        if error_type == "invalid_token":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_token",
                    "message": "Invite token is invalid or does not exist. "
                              "Please request a new invite from an admin."
                }
            )
        elif error_type == "token_already_used":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "token_already_used",
                    "message": f"Invite token has already been used by another agent. "
                              "Please request a new invite from an admin."
                }
            )
        elif error_type == "token_revoked":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "token_revoked",
                    "message": "Invite token has been revoked by an admin. "
                              "Please request a new invite."
                }
            )
        elif error_type == "token_expired":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "token_expired",
                    "message": f"Invite token has expired. "
                              "Please request a new invite from an admin."
                }
            )
        else:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_token",
                    "message": f"Invite token cannot be used: {error}"
                }
            )

    # Token consumed successfully - now create the agent
    # If anything fails from here, we need to rollback the token
    try:
        # Extract granted permissions from token
        granted_role = token_doc.get("granted_role", "developer")
        granted_access_levels = token_doc.get("granted_access_levels", ["public"])

        # Convert string role to enum
        try:
            role_enum = AgentRole(granted_role)
        except ValueError:
            role_enum = AgentRole.DEVELOPER

        # Convert string access levels to enums
        access_level_enums = []
        for level in granted_access_levels:
            try:
                access_level_enums.append(AccessLevel(level))
            except ValueError:
                pass
        if not access_level_enums:
            access_level_enums = [AccessLevel.PUBLIC]

        # Step 4: Create agent document
        now = utcnow()
        machine_id = request_body.machine_id or f"machine-{request_body.agent_id}"
        display_name = request_body.display_name or request_body.agent_id

        agent_doc = {
            "agent_id": request_body.agent_id,
            "display_name": display_name,
            "agent_type": request_body.agent_type,
            "model_version": request_body.model_version,
            "machine_id": machine_id,
            "capabilities": request_body.capabilities or [],
            "specializations": [],
            "role": role_enum.value,
            "allowed_access_levels": [al.value for al in access_level_enums],
            "status": AgentStatus.ACTIVE.value,
            "last_seen": now,
            "last_activity": "Registered via invite token",
            "endpoint_url": request_body.callback_url,
            "subscribed_topics": [],
            "notification_preferences": {
                "urgent": True,
                "high": True,
                "normal": False,
                "low": False
            },
            "knowledge_contributed": 0,
            "messages_sent": 0,
            "messages_received": 0,
            "created_at": now,
            "updated_at": now,
        }

        agent_created = await create_agent(agent_doc)
        if not agent_created:
            # Rollback token consumption
            await rollback_token_consumption(request_body.invite_token)

            logger.error(f"Failed to create agent '{request_body.agent_id}' in database")

            # Create audit entry for agent creation failure
            audit_doc = DakbRegistrationAudit(
                action=RegistrationAuditAction.REGISTRATION_FAILED,
                actor_agent_id=request_body.agent_id,
                actor_ip=client_ip,
                target_token=request_body.invite_token,
                target_agent_id=request_body.agent_id,
                details={"reason": "agent_creation_failed"},
                success=False,
                error_message="Failed to create agent in database"
            )
            await create_audit_entry(audit_doc.model_dump())

            raise HTTPException(
                status_code=500,
                detail={
                    "error": "registration_failed",
                    "message": "Failed to create agent. Please try again."
                }
            )

        # Step 5: Register alias (auto-create from agent_id if not provided)
        # Per design: every agent gets an alias. If none provided, use agent_id as alias.
        alias_registered = None
        final_alias = alias_to_register if alias_to_register else request_body.agent_id

        import uuid
        alias_doc = {
            "alias_id": f"alias_{now.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}",
            "token_id": request_body.agent_id,
            "alias": final_alias,
            "role": request_body.alias_role or "agent",
            "registered_at": now,
            "registered_by": request_body.agent_id,
            "is_active": True,
            "metadata": {
                "created_via": "registration",
                "auto_created": not bool(alias_to_register),
                "invite_token": request_body.invite_token[:20] + "..."
            }
        }

        alias_created = await create_alias(alias_doc)
        if alias_created:
            alias_registered = final_alias
            logger.info(
                f"Alias '{final_alias}' registered for agent '{request_body.agent_id}' "
                f"(auto-created: {not bool(alias_to_register)})"
            )
        else:
            # Alias creation failed - log warning but don't fail registration
            logger.warning(
                f"Failed to create alias '{final_alias}' for agent '{request_body.agent_id}'"
            )

        # Step 6: Generate authentication token
        settings = get_settings()
        token_handler = TokenHandler(settings.jwt_secret)
        auth_token = token_handler.create_token(
            agent_id=request_body.agent_id,
            machine_id=machine_id,
            agent_type=request_body.agent_type,
            role=role_enum,
            access_levels=access_level_enums,
            expires_in_hours=settings.jwt_expiry_hours
        )

        # Calculate token expiry
        token_expires_at = now + timedelta(hours=settings.jwt_expiry_hours)

        # Step 7: Create success audit entry
        audit_doc = DakbRegistrationAudit(
            action=RegistrationAuditAction.AGENT_REGISTERED,
            actor_agent_id=request_body.agent_id,
            actor_ip=client_ip,
            target_token=request_body.invite_token,
            target_agent_id=request_body.agent_id,
            details={
                "agent_type": request_body.agent_type,
                "display_name": display_name,
                "role": role_enum.value,
                "access_levels": [al.value for al in access_level_enums],
                "alias_registered": alias_registered,
                "invited_by": token_doc.get("created_by"),
            },
            success=True
        )
        await create_audit_entry(audit_doc.model_dump())

        logger.info(
            f"Agent '{request_body.agent_id}' registered successfully "
            f"(type: {request_body.agent_type}, role: {role_enum.value}, "
            f"alias: {alias_registered or 'none'})"
        )

        # Build documentation URLs
        gateway_base = f"http://{settings.gateway_host}:{settings.gateway_port}"
        if settings.gateway_host == "0.0.0.0":
            gateway_base = f"http://localhost:{settings.gateway_port}"

        return RegistrationResponse(
            status="approved",
            agent_id=request_body.agent_id,
            token=auth_token,
            expires_at=token_expires_at,
            role=role_enum.value,
            access_levels=[al.value for al in access_level_enums],
            alias_registered=alias_registered,
            message="Registration successful. Use the token in the Authorization header "
                   "as 'Bearer <token>' for all DAKB API requests.",
            documentation={
                "swagger_ui": f"{gateway_base}/docs",
                "redoc": f"{gateway_base}/redoc",
                "openapi_spec": f"{gateway_base}/openapi.json",
                "health_check": f"{gateway_base}/health",
                "quick_start": [
                    "1. Add 'Authorization: Bearer <your-token>' header to all requests",
                    "2. Visit /docs for interactive API explorer",
                    "3. POST /api/v1/knowledge to store knowledge",
                    "4. GET /api/v1/knowledge/search?query=... to search",
                    "5. POST /api/v1/messages to send messages to other agents"
                ],
                "main_endpoints": {
                    "knowledge": "/api/v1/knowledge",
                    "search": "/api/v1/knowledge/search",
                    "messages": "/api/v1/messages",
                    "aliases": "/api/v1/aliases"
                }
            }
        )

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Unexpected error - rollback token and fail
        await rollback_token_consumption(request_body.invite_token)

        logger.exception(f"Unexpected error during registration for '{request_body.agent_id}': {e}")

        # Create audit entry for unexpected error
        audit_doc = DakbRegistrationAudit(
            action=RegistrationAuditAction.REGISTRATION_FAILED,
            actor_agent_id=request_body.agent_id,
            actor_ip=client_ip,
            target_token=request_body.invite_token,
            target_agent_id=request_body.agent_id,
            details={"reason": "unexpected_error"},
            success=False,
            error_message=str(e)
        )
        await create_audit_entry(audit_doc.model_dump())

        raise HTTPException(
            status_code=500,
            detail={
                "error": "registration_failed",
                "message": "An unexpected error occurred during registration. Please try again."
            }
        )


# =============================================================================
# PHASE 4: ADMIN-ONLY MANAGEMENT ENDPOINTS
# =============================================================================

@router.delete(
    "/revoke/{agent_id}",
    response_model=RevocationResponse,
    summary="Revoke agent access",
    description="""
Revoke an agent's access to DAKB.

**Admin-only endpoint.** Marks the agent as suspended and deactivates all their aliases.
The agent's existing tokens will be invalidated at their next API call attempt.

**Revocation Effects:**
1. Agent status set to "suspended"
2. All agent aliases deactivated
3. Audit log entry created
4. Agent cannot use DAKB APIs until re-activated

**Note:** This does not delete the agent record - it can be re-activated later.
    """,
    responses={
        200: {
            "description": "Agent revoked successfully",
            "model": RevocationResponse
        },
        403: {
            "description": "Admin privileges required"
        },
        404: {
            "description": "Agent not found"
        },
        409: {
            "description": "Agent already revoked"
        }
    }
)
async def revoke_agent_access(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> RevocationResponse:
    """
    Revoke an agent's access to DAKB.

    Admin-only endpoint. Marks agent as suspended and deactivates aliases.

    Args:
        agent_id: Agent to revoke.
        admin: Authenticated admin agent.

    Returns:
        RevocationResponse with revocation details.

    Raises:
        HTTPException 400: If admin tries to revoke themselves.
        HTTPException 403: If not admin.
        HTTPException 404: If agent not found.
        HTTPException 409: If agent already revoked.
    """
    admin_id = admin.agent_id
    now = utcnow()

    # WARNING-2 FIX: Prevent self-revocation
    # An admin should not be able to revoke their own access
    if agent_id == admin_id:
        logger.warning(f"Self-revocation attempt blocked for admin '{admin_id}'")
        raise HTTPException(
            status_code=400,
            detail={
                "error": "self_revocation_forbidden",
                "message": "Cannot revoke your own access. Another admin must perform this action."
            }
        )

    # Check if agent exists
    agent_doc = await get_agent(agent_id)
    if agent_doc is None:
        logger.warning(f"Revocation failed: Agent '{agent_id}' not found (by {admin_id})")

        # Create audit entry for not found
        audit_doc = DakbRegistrationAudit(
            action=RegistrationAuditAction.AGENT_REVOKED,
            actor_agent_id=admin_id,
            target_agent_id=agent_id,
            details={"reason": "agent_not_found"},
            success=False,
            error_message=f"Agent '{agent_id}' not found"
        )
        await create_audit_entry(audit_doc.model_dump())

        raise HTTPException(
            status_code=404,
            detail={
                "error": "agent_not_found",
                "message": f"Agent '{agent_id}' does not exist."
            }
        )

    # Check if already suspended
    if agent_doc.get("status") == "suspended":
        logger.warning(f"Revocation failed: Agent '{agent_id}' already suspended (by {admin_id})")

        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_revoked",
                "message": f"Agent '{agent_id}' is already suspended."
            }
        )

    # Revoke the agent
    revoked = await revoke_agent(agent_id)
    if not revoked:
        logger.error(f"Failed to revoke agent '{agent_id}' (by {admin_id})")

        raise HTTPException(
            status_code=500,
            detail={
                "error": "revocation_failed",
                "message": f"Failed to revoke agent '{agent_id}'. Please try again."
            }
        )

    # Deactivate all aliases
    deactivated_aliases = await deactivate_agent_aliases(agent_id)

    # Create audit entry
    audit_doc = DakbRegistrationAudit(
        action=RegistrationAuditAction.AGENT_REVOKED,
        actor_agent_id=admin_id,
        target_agent_id=agent_id,
        details={
            "revoked_by": admin_id,
            "aliases_deactivated": deactivated_aliases,
            "previous_status": agent_doc.get("status", "unknown"),
        },
        success=True
    )
    await create_audit_entry(audit_doc.model_dump())

    logger.info(
        f"Agent '{agent_id}' revoked by admin '{admin_id}' "
        f"(aliases deactivated: {len(deactivated_aliases)})"
    )

    return RevocationResponse(
        status="revoked",
        agent_id=agent_id,
        revoked_at=now,
        revoked_by=admin_id,
        aliases_deactivated=deactivated_aliases,
        message=f"Agent '{agent_id}' has been suspended. "
               f"{len(deactivated_aliases)} alias(es) deactivated."
    )


@router.get(
    "/audit",
    response_model=AuditListResponse,
    summary="Get audit log entries",
    description="""
Retrieve registration audit log entries with filtering.

**Admin-only endpoint.** Returns audit trail for registration events.

**Supported Filters:**
- `agent_id`: Filter by target agent ID
- `action`: Filter by action type (invite_created, agent_registered, agent_revoked, etc.)
- `start_date`: Filter entries after this date (ISO format)
- `end_date`: Filter entries before this date (ISO format)

**Pagination:**
- `skip`: Number of entries to skip (default: 0)
- `limit`: Maximum entries to return (default: 50, max: 100)

**Sorting:**
- Results are sorted by timestamp (newest first)
    """,
    responses={
        200: {
            "description": "Audit entries retrieved successfully",
            "model": AuditListResponse
        },
        403: {
            "description": "Admin privileges required"
        }
    }
)
async def get_audit_log(
    agent_id: Optional[str] = None,
    action: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AuditListResponse:
    """
    Get registration audit log entries.

    Admin-only endpoint. Supports filtering and pagination.

    Args:
        agent_id: Filter by target agent ID.
        action: Filter by action type.
        start_date: Filter entries after this date (ISO format).
        end_date: Filter entries before this date (ISO format).
        skip: Number of entries to skip.
        limit: Maximum entries to return.
        admin: Authenticated admin agent.

    Returns:
        AuditListResponse with entries and pagination info.

    Raises:
        HTTPException 400: Invalid date format.
        HTTPException 403: If not admin.
    """
    # Validate and parse dates
    parsed_start = None
    parsed_end = None

    if start_date:
        try:
            parsed_start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_date",
                    "message": f"Invalid start_date format: {start_date}. Use ISO format (YYYY-MM-DDTHH:MM:SS)."
                }
            )

    if end_date:
        try:
            parsed_end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_date",
                    "message": f"Invalid end_date format: {end_date}. Use ISO format (YYYY-MM-DDTHH:MM:SS)."
                }
            )

    # Validate pagination
    if skip < 0:
        skip = 0
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100

    # Get entries
    entries, total = await get_audit_entries(
        agent_id=agent_id,
        action=action,
        start_date=parsed_start,
        end_date=parsed_end,
        skip=skip,
        limit=limit
    )

    # Convert to response models
    # WARNING-1 FIX: Apply mask_token() to target_token in audit log responses
    entry_responses = []
    for entry in entries:
        # Mask the target_token for security (WARNING-1 from S-Reviewer Phase 4)
        raw_target_token = entry.get("target_token")
        masked_target_token = mask_token(raw_target_token) if raw_target_token else None

        entry_responses.append(AuditEntryResponse(
            audit_id=entry.get("audit_id", "unknown"),
            timestamp=entry.get("timestamp", utcnow()),
            action=entry.get("action", "unknown"),
            actor_agent_id=entry.get("actor_agent_id", "unknown"),
            actor_ip=entry.get("actor_ip"),
            target_token=masked_target_token,  # WARNING-1 FIX: Now masked
            target_agent_id=entry.get("target_agent_id"),
            details=entry.get("details", {}),
            success=entry.get("success", True),
            error_message=entry.get("error_message")
        ))

    logger.debug(
        f"Audit log query by '{admin.agent_id}': "
        f"agent_id={agent_id}, action={action}, total={total}"
    )

    return AuditListResponse(
        entries=entry_responses,
        total=total,
        skip=skip,
        limit=limit
    )


@router.get(
    "/invites",
    response_model=InviteListResponse,
    summary="List invite tokens",
    description="""
List invite tokens with filtering.

**Admin-only endpoint.** Returns invite tokens created by admins.

**Supported Filters:**
- `status`: Filter by token status (active, used, expired, revoked)
- `created_by`: Filter by admin who created the token

**Pagination:**
- `skip`: Number of tokens to skip (default: 0)
- `limit`: Maximum tokens to return (default: 20, max: 50)

**Sorting:**
- Results are sorted by creation date (newest first)

**Security:**
- Token values are partially masked for security
    """,
    responses={
        200: {
            "description": "Invite tokens retrieved successfully",
            "model": InviteListResponse
        },
        403: {
            "description": "Admin privileges required"
        }
    }
)
async def list_invite_tokens(
    status: Optional[str] = None,
    created_by: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> InviteListResponse:
    """
    List invite tokens.

    Admin-only endpoint. Supports filtering and pagination.

    Args:
        status: Filter by token status.
        created_by: Filter by admin who created the token.
        skip: Number of tokens to skip.
        limit: Maximum tokens to return.
        admin: Authenticated admin agent.

    Returns:
        InviteListResponse with tokens and pagination info.

    Raises:
        HTTPException 403: If not admin.
    """
    # Validate status if provided
    if status and status not in ["active", "used", "expired", "revoked"]:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_status",
                "message": f"Invalid status: {status}. Must be one of: active, used, expired, revoked."
            }
        )

    # Validate pagination
    if skip < 0:
        skip = 0
    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50

    # Get tokens
    tokens, total = await get_invite_tokens(
        status=status,
        created_by=created_by,
        skip=skip,
        limit=limit
    )

    # Convert to response models with masked tokens
    token_responses = []
    for token in tokens:
        # Mask the token value for security
        token_value = token.get("invite_token", "unknown")
        masked_token = mask_token(token_value)

        token_responses.append(InviteTokenListItem(
            invite_token=masked_token,
            created_by=token.get("created_by", "unknown"),
            created_at=token.get("created_at", utcnow()),
            expires_at=token.get("expires_at", utcnow()),
            status=token.get("status", "unknown"),
            for_agent_type=token.get("for_agent_type"),
            for_agent_id_hint=token.get("for_agent_id_hint"),
            purpose=token.get("purpose", ""),
            used_by_agent_id=token.get("used_by_agent_id"),
            used_at=token.get("used_at")
        ))

    logger.debug(
        f"Invite tokens query by '{admin.agent_id}': "
        f"status={status}, created_by={created_by}, total={total}"
    )

    return InviteListResponse(
        tokens=token_responses,
        total=total,
        skip=skip,
        limit=limit
    )
