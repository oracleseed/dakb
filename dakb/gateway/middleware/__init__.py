# DAKB Gateway Middleware
"""
Authentication and security middleware.

This module provides JWT authentication, rate limiting, and access control
for the DAKB Gateway API.

Components:
- auth: JWT authentication and 3-tier access control
- RateLimiter: Token bucket rate limiting per agent

Usage:
    from backend.dakb_service.gateway.middleware.auth import (
        get_current_agent,
        check_rate_limit,
        require_role,
        generate_agent_token,
        AccessChecker,
    )
"""

from .auth import (
    # Access control
    AccessChecker,
    AuthenticatedAgent,
    # JWT
    JWTHandler,
    # Rate limiting
    RateLimiter,
    TokenHandler,
    # Models
    TokenPayload,
    # FastAPI dependencies
    bearer_scheme,
    check_rate_limit,
    # Utilities
    generate_agent_token,
    get_current_agent,
    get_rate_limiter,
    require_access_level,
    require_role,
)

__all__ = [
    # Models
    "TokenPayload",
    "AuthenticatedAgent",
    # Rate limiting
    "RateLimiter",
    "get_rate_limiter",
    # JWT
    "JWTHandler",
    "TokenHandler",
    # FastAPI dependencies
    "bearer_scheme",
    "get_current_agent",
    "check_rate_limit",
    # Access control
    "AccessChecker",
    "require_role",
    "require_access_level",
    # Utilities
    "generate_agent_token",
]
