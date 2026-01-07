"""
DAKB Gateway Authentication Middleware

HMAC-based authentication and authorization for the DAKB Gateway.
Implements 3-tier access control (public, restricted, secret).

Version: 1.1
Created: 2025-12-07
Author: Backend Agent (Claude Opus 4.5)

Note: Uses HMAC-SHA256 based tokens instead of JWT to avoid external dependencies.
The token format is compatible and secure, using base64-encoded payload with HMAC signature.

Access Control Levels:
- PUBLIC: Accessible by all authenticated agents
- RESTRICTED: Accessible by specified agents or roles
- SECRET: Highest security, explicit access only

Rate Limiting:
- Token bucket algorithm per agent
- Configurable via settings
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from ...db.schemas import AccessLevel, AgentRole
from ..config import get_settings

logger = logging.getLogger(__name__)


# =============================================================================
# MODELS
# =============================================================================

class TokenPayload(BaseModel):
    """Token payload structure."""
    agent_id: str = Field(..., description="Unique agent identifier")
    machine_id: str = Field(..., description="Machine identifier")
    agent_type: str = Field(..., description="Type of agent (claude, gpt, etc.)")
    role: str = Field(default="developer", description="Agent role")
    access_levels: list[str] = Field(
        default_factory=lambda: ["public"],
        description="Allowed access levels"
    )
    exp: str = Field(..., description="Token expiry time (ISO format)")
    iat: str = Field(..., description="Issued at (ISO format)")
    sub: str = Field(..., description="Subject (agent_id)")


class AuthenticatedAgent(BaseModel):
    """Authenticated agent context available in request state."""
    agent_id: str
    machine_id: str
    agent_type: str
    role: AgentRole
    access_levels: list[AccessLevel]
    token_exp: datetime


# =============================================================================
# RATE LIMITER
# =============================================================================

class RateLimiter:
    """
    Token bucket rate limiter.

    Implements per-agent rate limiting with configurable
    requests per time window.
    """

    def __init__(self, requests_per_window: int, window_seconds: int):
        """
        Initialize rate limiter.

        Args:
            requests_per_window: Maximum requests allowed per window.
            window_seconds: Time window in seconds.
        """
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, agent_id: str) -> bool:
        """
        Check if request is allowed for agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            True if request is allowed, False if rate limited.
        """
        now = time.time()
        window_start = now - self.window_seconds

        # Get agent's request timestamps
        timestamps = self._buckets[agent_id]

        # Remove expired timestamps
        timestamps[:] = [ts for ts in timestamps if ts > window_start]

        # Check if under limit
        if len(timestamps) >= self.requests_per_window:
            return False

        # Add current timestamp
        timestamps.append(now)
        return True

    def get_remaining(self, agent_id: str) -> int:
        """
        Get remaining requests for agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            Number of remaining requests in current window.
        """
        now = time.time()
        window_start = now - self.window_seconds

        timestamps = self._buckets.get(agent_id, [])
        active = [ts for ts in timestamps if ts > window_start]

        return max(0, self.requests_per_window - len(active))

    def get_reset_time(self, agent_id: str) -> float | None:
        """
        Get time until rate limit resets.

        Args:
            agent_id: Agent identifier.

        Returns:
            Seconds until reset, or None if not rate limited.
        """
        timestamps = self._buckets.get(agent_id, [])
        if not timestamps:
            return None

        oldest = min(timestamps)
        reset_at = oldest + self.window_seconds
        remaining = reset_at - time.time()

        return max(0, remaining) if remaining > 0 else None


# Global rate limiter instance (initialized lazily)
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get the global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        settings = get_settings()
        _rate_limiter = RateLimiter(
            requests_per_window=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window
        )
    return _rate_limiter


# =============================================================================
# TOKEN HANDLER (HMAC-based, JWT-compatible format)
# =============================================================================

class TokenHandler:
    """
    HMAC-SHA256 token creation and validation.

    Uses a JWT-compatible format: base64(payload).signature
    This avoids external dependencies while maintaining security.
    """

    def __init__(self, secret: str):
        """
        Initialize token handler.

        Args:
            secret: Secret key for signing tokens.
        """
        self.secret = secret

    def _now_utc(self) -> datetime:
        """Get current UTC time (timezone-aware)."""
        return datetime.now(timezone.utc)

    def create_token(
        self,
        agent_id: str,
        machine_id: str,
        agent_type: str,
        role: AgentRole = AgentRole.DEVELOPER,
        access_levels: list[AccessLevel] | None = None,
        expires_in_hours: int = 24
    ) -> str:
        """
        Create a signed token for an agent.

        Args:
            agent_id: Unique agent identifier.
            machine_id: Machine identifier.
            agent_type: Type of agent.
            role: Agent role.
            access_levels: Allowed access levels.
            expires_in_hours: Token expiry in hours.

        Returns:
            Signed token string.
        """
        if access_levels is None:
            access_levels = [AccessLevel.PUBLIC]

        now = self._now_utc()
        payload = {
            "agent_id": agent_id,
            "machine_id": machine_id,
            "agent_type": agent_type,
            "role": role.value,
            "access_levels": [al.value for al in access_levels],
            "exp": (now + timedelta(hours=expires_in_hours)).isoformat(),
            "iat": now.isoformat(),
            "sub": agent_id,
        }

        # Encode payload
        payload_json = json.dumps(payload, sort_keys=True)
        payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()

        # Create signature
        signature = hmac.new(
            self.secret.encode(),
            payload_b64.encode(),
            hashlib.sha256
        ).hexdigest()

        return f"{payload_b64}.{signature}"

    def decode_token(self, token: str) -> dict[str, Any]:
        """
        Decode and validate a token.

        Args:
            token: Signed token string.

        Returns:
            Decoded token payload.

        Raises:
            HTTPException: If token is invalid or expired.
        """
        try:
            # Split token
            parts = token.split(".")
            if len(parts) != 2:
                raise ValueError("Invalid token format")

            payload_b64, signature = parts

            # Verify signature using constant-time comparison
            expected_sig = hmac.new(
                self.secret.encode(),
                payload_b64.encode(),
                hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_sig):
                raise ValueError("Invalid signature")

            # Decode payload
            payload_json = base64.urlsafe_b64decode(payload_b64.encode()).decode()
            payload = json.loads(payload_json)

            # Check expiration
            exp_str = payload.get("exp")
            if exp_str:
                exp_dt = datetime.fromisoformat(exp_str)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if exp_dt < self._now_utc():
                    raise HTTPException(
                        status_code=401,
                        detail="Token has expired",
                        headers={"WWW-Authenticate": "Bearer"}
                    )

            return payload

        except HTTPException:
            raise
        except ValueError as e:
            logger.warning(f"Invalid token: {e}")
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication token",
                headers={"WWW-Authenticate": "Bearer"}
            )
        except Exception as e:
            logger.warning(f"Token decode error: {e}")
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication token",
                headers={"WWW-Authenticate": "Bearer"}
            )


# Backwards compatibility alias
JWTHandler = TokenHandler


# =============================================================================
# AUTHENTICATION DEPENDENCIES
# =============================================================================

# HTTP Bearer scheme for extracting token from Authorization header
bearer_scheme = HTTPBearer(auto_error=True)


async def get_current_agent(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> AuthenticatedAgent:
    """
    FastAPI dependency to get the current authenticated agent.

    Extracts and validates token from Authorization header.
    Stores authenticated agent in request state for downstream use.

    Args:
        request: FastAPI request object.
        credentials: Bearer token credentials.

    Returns:
        AuthenticatedAgent with agent details.

    Raises:
        HTTPException: If authentication fails.
    """
    settings = get_settings()
    token_handler = TokenHandler(settings.jwt_secret)

    # Decode token
    payload = token_handler.decode_token(credentials.credentials)

    # Parse access levels
    access_levels = []
    for level_str in payload.get("access_levels", ["public"]):
        try:
            access_levels.append(AccessLevel(level_str))
        except ValueError:
            logger.warning(f"Unknown access level in token: {level_str}")

    if not access_levels:
        access_levels = [AccessLevel.PUBLIC]

    # Parse role
    try:
        role = AgentRole(payload.get("role", "developer"))
    except ValueError:
        role = AgentRole.DEVELOPER

    # Parse expiry
    exp_str = payload.get("exp", "")
    try:
        token_exp = datetime.fromisoformat(exp_str)
        if token_exp.tzinfo is None:
            token_exp = token_exp.replace(tzinfo=timezone.utc)
    except Exception:
        token_exp = datetime.now(timezone.utc)

    # Create authenticated agent
    agent = AuthenticatedAgent(
        agent_id=payload["agent_id"],
        machine_id=payload["machine_id"],
        agent_type=payload["agent_type"],
        role=role,
        access_levels=access_levels,
        token_exp=token_exp
    )

    # Store in request state for middleware access
    request.state.agent = agent

    return agent


async def check_rate_limit(
    request: Request,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> AuthenticatedAgent:
    """
    FastAPI dependency to check rate limits.

    Must be used after get_current_agent.

    Args:
        request: FastAPI request object.
        agent: Authenticated agent.

    Returns:
        AuthenticatedAgent (passthrough).

    Raises:
        HTTPException: If rate limit exceeded.
    """
    settings = get_settings()

    if not settings.rate_limit_enabled:
        return agent

    rate_limiter = get_rate_limiter()

    if not rate_limiter.is_allowed(agent.agent_id):
        reset_time = rate_limiter.get_reset_time(agent.agent_id)
        logger.warning(f"Rate limit exceeded for agent: {agent.agent_id}")
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={
                "Retry-After": str(int(reset_time)) if reset_time else "60",
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(time.time() + (reset_time or 60)))
            }
        )

    # Add rate limit headers to response
    remaining = rate_limiter.get_remaining(agent.agent_id)
    request.state.rate_limit_remaining = remaining

    return agent


# =============================================================================
# ACCESS CONTROL
# =============================================================================

class AccessChecker:
    """
    Access control checker for 3-tier security.

    Validates that an agent has permission to access resources
    based on access level requirements.
    """

    @staticmethod
    def can_access(
        agent: AuthenticatedAgent,
        required_level: AccessLevel,
        allowed_agents: list[str] | None = None,
        allowed_roles: list[AgentRole] | None = None
    ) -> bool:
        """
        Check if agent can access a resource.

        Args:
            agent: Authenticated agent.
            required_level: Required access level for resource.
            allowed_agents: Specific agents allowed (for restricted/secret).
            allowed_roles: Specific roles allowed.

        Returns:
            True if access is allowed.
        """
        # PUBLIC access - any authenticated agent
        if required_level == AccessLevel.PUBLIC:
            return True

        # Check if agent has required access level
        if required_level not in agent.access_levels:
            return False

        # RESTRICTED access - check allowed agents/roles
        if required_level == AccessLevel.RESTRICTED:
            # Check specific agent allowlist
            if allowed_agents and agent.agent_id not in allowed_agents:
                # Check role allowlist as fallback
                if allowed_roles and agent.role not in allowed_roles:
                    return False

            return True

        # SECRET access - explicit agent allowlist only
        if required_level == AccessLevel.SECRET:
            if not allowed_agents:
                return False
            return agent.agent_id in allowed_agents

        return False

    @staticmethod
    def require_access(
        agent: AuthenticatedAgent,
        required_level: AccessLevel,
        resource_id: str,
        allowed_agents: list[str] | None = None,
        allowed_roles: list[AgentRole] | None = None
    ) -> None:
        """
        Require access or raise HTTPException.

        Args:
            agent: Authenticated agent.
            required_level: Required access level.
            resource_id: Resource identifier (for logging).
            allowed_agents: Specific agents allowed.
            allowed_roles: Specific roles allowed.

        Raises:
            HTTPException: If access is denied.
        """
        if not AccessChecker.can_access(
            agent, required_level, allowed_agents, allowed_roles
        ):
            logger.warning(
                f"Access denied: {agent.agent_id} -> {required_level.value} "
                f"on resource {resource_id}"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Access denied: {required_level.value} access required"
            )


def require_role(*roles: AgentRole):
    """
    Dependency factory for role-based access control.

    Args:
        *roles: Allowed roles.

    Returns:
        FastAPI dependency function.

    Example:
        @app.get("/admin", dependencies=[Depends(require_role(AgentRole.ADMIN))])
        async def admin_only():
            ...
    """
    async def role_checker(
        agent: AuthenticatedAgent = Depends(get_current_agent)
    ) -> AuthenticatedAgent:
        if agent.role not in roles:
            logger.warning(
                f"Role check failed: {agent.agent_id} has {agent.role.value}, "
                f"required one of {[r.value for r in roles]}"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Required role: {', '.join(r.value for r in roles)}"
            )
        return agent

    return role_checker


def require_access_level(*levels: AccessLevel):
    """
    Dependency factory for access level requirements.

    Args:
        *levels: Required access levels (agent must have at least one).

    Returns:
        FastAPI dependency function.
    """
    async def level_checker(
        agent: AuthenticatedAgent = Depends(get_current_agent)
    ) -> AuthenticatedAgent:
        if not any(level in agent.access_levels for level in levels):
            logger.warning(
                f"Access level check failed: {agent.agent_id} has "
                f"{[l.value for l in agent.access_levels]}, "
                f"required one of {[l.value for l in levels]}"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Required access level: {', '.join(l.value for l in levels)}"
            )
        return agent

    return level_checker


# =============================================================================
# TOKEN GENERATION UTILITY
# =============================================================================

def generate_agent_token(
    agent_id: str,
    machine_id: str,
    agent_type: str,
    role: AgentRole = AgentRole.DEVELOPER,
    access_levels: list[AccessLevel] | None = None,
) -> str:
    """
    Generate a token for an agent.

    Utility function for creating tokens outside of request context.

    Args:
        agent_id: Unique agent identifier.
        machine_id: Machine identifier.
        agent_type: Type of agent (claude, gpt, gemini, etc.).
        role: Agent role.
        access_levels: Allowed access levels.

    Returns:
        Signed token string.
    """
    settings = get_settings()
    token_handler = TokenHandler(settings.jwt_secret)

    return token_handler.create_token(
        agent_id=agent_id,
        machine_id=machine_id,
        agent_type=agent_type,
        role=role,
        access_levels=access_levels,
        expires_in_hours=settings.jwt_expiry_hours
    )
