"""
DAKB Gateway Alias Registration Routes

REST API routes for agent alias management in the Token Team system.
Enables one token to register multiple aliases for flexible message routing.

Version: 1.0
Created: 2025-12-11
Author: Backend Agent (Claude Opus 4.5)

Endpoints:
- POST   /api/v1/aliases              - Register new alias
- GET    /api/v1/aliases              - List aliases for current token
- DELETE /api/v1/aliases/{alias}      - Deactivate alias (soft delete)
- GET    /api/v1/aliases/resolve/{alias} - Resolve alias to token_id (public)

Access Control:
- All endpoints except resolve require JWT authentication
- Token can only manage its own aliases (ownership enforcement)
- Resolve endpoint is public for message routing lookups
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from ..config import get_settings
from ..middleware.auth import (
    AuthenticatedAgent,
    get_current_agent,
    check_rate_limit,
)
from ...db import (
    # Schemas
    DakbAgentAlias,
    AliasCreate,
    # Repositories
    get_dakb_repositories,
)
from ...db.collections import get_dakb_client

logger = logging.getLogger(__name__)


# =============================================================================
# ROUTER
# =============================================================================

router = APIRouter(
    prefix="/api/v1/aliases",
    tags=["Aliases"],
    dependencies=[Depends(check_rate_limit)],  # Rate limiting on all routes
)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class RegisterAliasRequest(BaseModel):
    """Request model for registering a new alias."""
    alias: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Alias name (must be globally unique)"
    )
    role: Optional[str] = Field(
        None,
        max_length=100,
        description="Optional role for the alias (e.g., 'orchestration', 'code_review')"
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata (e.g., display_name, description)"
    )


class RegisterAliasResponse(BaseModel):
    """Response model for successful alias registration."""
    alias_id: str = Field(..., description="Unique alias identifier")
    token_id: str = Field(..., description="Owning token identity")
    alias: str = Field(..., description="Registered alias name")
    role: Optional[str] = Field(None, description="Associated role")
    is_active: bool = Field(..., description="Whether alias is active")
    message: str = Field(..., description="Status message")


class AliasListResponse(BaseModel):
    """Response model for listing aliases."""
    aliases: list[DakbAgentAlias] = Field(
        default_factory=list,
        description="List of alias records"
    )
    total: int = Field(default=0, description="Total number of aliases")
    token_id: str = Field(..., description="Token identity")


class ResolveAliasResponse(BaseModel):
    """Response model for alias resolution."""
    token_id: str = Field(..., description="Token ID that owns the alias")
    alias: str = Field(..., description="Resolved alias name")


class DeactivateAliasResponse(BaseModel):
    """Response model for alias deactivation."""
    alias: str = Field(..., description="Deactivated alias name")
    message: str = Field(..., description="Status message")


class AliasAvailabilityResponse(BaseModel):
    """Response model for alias availability check."""
    alias: str = Field(..., description="Alias name checked")
    available: bool = Field(..., description="Whether alias is available")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_repositories():
    """Get DAKB repository instances."""
    client = get_dakb_client()
    settings = get_settings()
    return get_dakb_repositories(client, settings.db_name)


# =============================================================================
# STATIC ROUTES (defined before parametric routes)
# =============================================================================

@router.get(
    "/resolve/{alias}",
    response_model=ResolveAliasResponse,
    summary="Resolve alias to token_id",
    description="Public endpoint to resolve an alias to its owning token_id for message routing."
)
async def resolve_alias(
    alias: str,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> ResolveAliasResponse:
    """
    Resolve an alias to its owning token_id.

    This is the primary endpoint for message routing. When sending a message,
    the system first attempts to resolve the recipient as an alias. If found,
    the message is routed to the alias owner's inbox.

    Args:
        alias: Alias name to resolve.
        agent: Authenticated agent (for rate limiting).

    Returns:
        ResolveAliasResponse with token_id and alias.

    Raises:
        HTTPException 404: If alias not found or inactive.
    """
    repos = get_repositories()

    token_id = repos["aliases"].resolve_alias(alias)

    if token_id is None:
        logger.debug(f"Alias resolution failed: '{alias}' not found or inactive")
        raise HTTPException(
            status_code=404,
            detail=f"Alias '{alias}' not found or inactive"
        )

    logger.info(f"Alias '{alias}' resolved to token '{token_id}' by {agent.agent_id}")

    return ResolveAliasResponse(
        token_id=token_id,
        alias=alias
    )


@router.get(
    "/check/{alias}",
    response_model=AliasAvailabilityResponse,
    summary="Check alias availability",
    description="Check if an alias name is available for registration."
)
async def check_alias_availability(
    alias: str,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> AliasAvailabilityResponse:
    """
    Check if an alias name is available for registration.

    Args:
        alias: Alias name to check.
        agent: Authenticated agent.

    Returns:
        AliasAvailabilityResponse indicating availability.
    """
    repos = get_repositories()

    available = repos["aliases"].is_alias_available(alias)

    return AliasAvailabilityResponse(
        alias=alias,
        available=available
    )


# =============================================================================
# CRUD ROUTES
# =============================================================================

@router.post(
    "",
    response_model=RegisterAliasResponse,
    status_code=201,
    summary="Register new alias",
    description="Register a new alias for the authenticated token."
)
async def register_alias(
    request: RegisterAliasRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> RegisterAliasResponse:
    """
    Register a new alias for the authenticated token.

    The alias must be globally unique across all tokens. Only the owning
    token can register aliases for itself, enforced by using the token_id
    from the JWT.

    Args:
        request: Alias registration data.
        agent: Authenticated agent (token_id extracted from JWT).

    Returns:
        RegisterAliasResponse with created alias details.

    Raises:
        HTTPException 409: If alias already exists (conflict).
        HTTPException 400: If validation fails.
    """
    repos = get_repositories()

    # Extract token_id from authenticated agent
    token_id = agent.agent_id

    # Check alias availability first
    if not repos["aliases"].is_alias_available(request.alias):
        logger.warning(
            f"Alias registration failed: '{request.alias}' already exists "
            f"(requested by token '{token_id}')"
        )
        raise HTTPException(
            status_code=409,
            detail=f"Alias '{request.alias}' is already registered. "
                   "Aliases must be globally unique."
        )

    try:
        # Register the alias
        alias_record = repos["aliases"].register_alias(
            token_id=token_id,
            alias=request.alias,
            role=request.role,
            metadata=request.metadata
        )

        logger.info(
            f"Alias registered: '{request.alias}' for token '{token_id}' "
            f"(role: {request.role or 'none'})"
        )

        return RegisterAliasResponse(
            alias_id=alias_record.alias_id,
            token_id=alias_record.token_id,
            alias=alias_record.alias,
            role=alias_record.role,
            is_active=alias_record.is_active,
            message=f"Alias '{request.alias}' registered successfully"
        )

    except DuplicateKeyError:
        # Race condition - alias was registered between check and insert
        logger.warning(
            f"Alias registration race condition: '{request.alias}' "
            f"was registered by another token"
        )
        raise HTTPException(
            status_code=409,
            detail=f"Alias '{request.alias}' was just registered by another token. "
                   "Please try a different alias."
        )
    except ValueError as e:
        logger.warning(f"Alias validation error: {e}")
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )


@router.get(
    "",
    response_model=AliasListResponse,
    summary="List aliases for current token",
    description="List all aliases registered to the authenticated token."
)
async def list_aliases(
    active_only: bool = Query(
        default=True,
        description="If true, only return active aliases"
    ),
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> AliasListResponse:
    """
    List all aliases registered to the authenticated token.

    Args:
        active_only: If true, only return active aliases (default: true).
        agent: Authenticated agent (token_id extracted from JWT).

    Returns:
        AliasListResponse with list of aliases.
    """
    repos = get_repositories()

    # Extract token_id from authenticated agent
    token_id = agent.agent_id

    aliases = repos["aliases"].get_aliases_for_token(
        token_id=token_id,
        active_only=active_only
    )

    logger.debug(
        f"Listed {len(aliases)} aliases for token '{token_id}' "
        f"(active_only={active_only})"
    )

    return AliasListResponse(
        aliases=aliases,
        total=len(aliases),
        token_id=token_id
    )


@router.delete(
    "/{alias}",
    response_model=DeactivateAliasResponse,
    summary="Deactivate alias",
    description="Deactivate (soft delete) an alias. Only the owning token can deactivate."
)
async def deactivate_alias(
    alias: str,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> DeactivateAliasResponse:
    """
    Deactivate an alias (soft delete).

    Only the owning token can deactivate its aliases. Deactivated aliases
    no longer route messages but remain in the database for audit purposes.

    Args:
        alias: Alias name to deactivate.
        agent: Authenticated agent (token_id extracted from JWT).

    Returns:
        DeactivateAliasResponse confirming deactivation.

    Raises:
        HTTPException 403: If token does not own the alias.
        HTTPException 404: If alias not found.
    """
    repos = get_repositories()

    # Extract token_id from authenticated agent
    token_id = agent.agent_id

    # First, check if the alias exists
    alias_record = repos["aliases"].get_by_alias(alias)

    if alias_record is None:
        logger.warning(
            f"Alias deactivation failed: '{alias}' not found "
            f"(requested by token '{token_id}')"
        )
        raise HTTPException(
            status_code=404,
            detail=f"Alias '{alias}' not found"
        )

    # Check ownership
    if alias_record.token_id != token_id:
        logger.warning(
            f"Alias deactivation denied: '{alias}' owned by '{alias_record.token_id}', "
            f"but request from token '{token_id}'"
        )
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: You do not own alias '{alias}'"
        )

    # Check if already inactive
    if not alias_record.is_active:
        logger.info(f"Alias '{alias}' is already inactive")
        return DeactivateAliasResponse(
            alias=alias,
            message=f"Alias '{alias}' was already inactive"
        )

    # Deactivate the alias
    success = repos["aliases"].deactivate_alias(token_id, alias)

    if not success:
        # This shouldn't happen given the checks above, but handle it
        logger.error(
            f"Unexpected failure deactivating alias '{alias}' "
            f"for token '{token_id}'"
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to deactivate alias"
        )

    logger.info(f"Alias '{alias}' deactivated by token '{token_id}'")

    return DeactivateAliasResponse(
        alias=alias,
        message=f"Alias '{alias}' has been deactivated"
    )


# =============================================================================
# ADDITIONAL UTILITY ROUTES
# =============================================================================

@router.post(
    "/{alias}/reactivate",
    response_model=RegisterAliasResponse,
    summary="Reactivate alias",
    description="Reactivate a previously deactivated alias."
)
async def reactivate_alias(
    alias: str,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> RegisterAliasResponse:
    """
    Reactivate a previously deactivated alias.

    Only the owning token can reactivate its aliases.

    Args:
        alias: Alias name to reactivate.
        agent: Authenticated agent (token_id extracted from JWT).

    Returns:
        RegisterAliasResponse with reactivated alias details.

    Raises:
        HTTPException 403: If token does not own the alias.
        HTTPException 404: If alias not found.
    """
    repos = get_repositories()

    # Extract token_id from authenticated agent
    token_id = agent.agent_id

    # First, check if the alias exists
    alias_record = repos["aliases"].get_by_alias(alias)

    if alias_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Alias '{alias}' not found"
        )

    # Check ownership
    if alias_record.token_id != token_id:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: You do not own alias '{alias}'"
        )

    # Check if already active
    if alias_record.is_active:
        return RegisterAliasResponse(
            alias_id=alias_record.alias_id,
            token_id=alias_record.token_id,
            alias=alias_record.alias,
            role=alias_record.role,
            is_active=alias_record.is_active,
            message=f"Alias '{alias}' is already active"
        )

    # Reactivate the alias
    success = repos["aliases"].reactivate_alias(token_id, alias)

    if not success:
        raise HTTPException(
            status_code=500,
            detail="Failed to reactivate alias"
        )

    # Fetch updated record
    updated_record = repos["aliases"].get_by_alias(alias)

    logger.info(f"Alias '{alias}' reactivated by token '{token_id}'")

    return RegisterAliasResponse(
        alias_id=updated_record.alias_id,
        token_id=updated_record.token_id,
        alias=updated_record.alias,
        role=updated_record.role,
        is_active=updated_record.is_active,
        message=f"Alias '{alias}' has been reactivated"
    )
