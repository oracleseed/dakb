"""
DAKB Gateway Moderation Routes

REST API routes for knowledge moderation operations.
All moderation routes require admin role authentication.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

ISS-048 Fix: Admin-only moderation enforcement at Gateway level.
All routes in this module use require_role(AgentRole.ADMIN) dependency.

Endpoints:
- POST   /api/v1/moderation/flag        - Flag knowledge for review
- GET    /api/v1/moderation/flags       - List pending flags (admin)
- POST   /api/v1/moderation/action      - Take moderation action (admin)
- GET    /api/v1/moderation/history     - Get moderation history (admin)
"""

import logging
from datetime import datetime
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..config import get_settings
from ..middleware.auth import (
    AuthenticatedAgent,
    get_current_agent,
    check_rate_limit,
    require_role,
    AccessChecker,
)
from ...db import (
    # Schemas
    DakbKnowledge,
    KnowledgeFlag,
    # Enums
    AccessLevel,
    KnowledgeStatus,
    AuditAction,
    ResourceType,
    AgentRole,
    FlagReason,
    ModerateAction,
    # Repositories
    get_dakb_repositories,
)
from ...db.collections import get_dakb_client

logger = logging.getLogger(__name__)


# =============================================================================
# ROUTER
# =============================================================================

router = APIRouter(
    prefix="/api/v1/moderation",
    tags=["Moderation"],
    dependencies=[Depends(check_rate_limit)],  # Rate limiting on all routes
)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class FlagRequest(BaseModel):
    """Request model for flagging knowledge."""
    knowledge_id: str = Field(..., description="Knowledge ID to flag")
    reason: FlagReason = Field(..., description="Reason for flagging")
    details: Optional[str] = Field(None, max_length=500, description="Additional details")


class FlagResponse(BaseModel):
    """Response model for flag creation."""
    flag_id: str
    knowledge_id: str
    reason: str
    status: str = "pending"
    flagged_by: str
    flagged_at: datetime


class FlagListResponse(BaseModel):
    """Response model for listing flags."""
    flags: list[dict[str, Any]]
    total: int
    pending_count: int


class ModerateRequest(BaseModel):
    """Request model for moderation action."""
    knowledge_id: str = Field(..., description="Knowledge ID to moderate")
    action: ModerateAction = Field(..., description="Moderation action to take")
    reason: Optional[str] = Field(None, max_length=500, description="Reason for action")


class ModerateResponse(BaseModel):
    """Response model for moderation action."""
    success: bool
    knowledge_id: str
    action: str
    new_status: str
    moderated_by: str
    moderated_at: datetime


class ModerationHistoryResponse(BaseModel):
    """Response model for moderation history."""
    history: list[dict[str, Any]]
    total: int


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_repositories():
    """Get DAKB repository instances."""
    client = get_dakb_client()
    settings = get_settings()
    return get_dakb_repositories(client, settings.db_name)


# =============================================================================
# ROUTES
# =============================================================================

@router.post(
    "/flag",
    response_model=FlagResponse,
    status_code=201,
    summary="Flag knowledge for review",
    description="Flag a knowledge entry for moderation review. Any authenticated agent can flag."
)
async def flag_for_review(
    request: FlagRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> FlagResponse:
    """
    Flag a knowledge entry for moderation review.

    Any authenticated agent can flag knowledge they can access.
    The flag will be reviewed by an admin moderator.

    Args:
        request: Flag creation request.
        agent: Authenticated agent.

    Returns:
        Created flag with ID and status.
    """
    repos = get_repositories()

    # Verify knowledge exists
    knowledge = repos["knowledge"].get_by_id(request.knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge not found")

    # Check agent can access this knowledge (must be able to read to flag)
    AccessChecker.require_access(
        agent,
        knowledge.access_level,
        request.knowledge_id,
        allowed_agents=knowledge.allowed_agents,
        allowed_roles=knowledge.allowed_roles
    )

    # Create flag using FlagRepository
    flag = repos["flags"].create_flag(
        knowledge_id=request.knowledge_id,
        flagged_by=agent.agent_id,
        reason=request.reason,
        details=request.details
    )

    # Audit log
    repos["audit"].log(
        agent_id=agent.agent_id,
        action=AuditAction.KNOWLEDGE_UPDATE,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=request.knowledge_id,
        details={
            "action": "flag",
            "flag_id": flag.flag_id,
            "reason": request.reason.value,
        },
        machine_id=agent.machine_id
    )

    logger.info(
        f"Knowledge {request.knowledge_id} flagged by {agent.agent_id} "
        f"for {request.reason.value}"
    )

    return FlagResponse(
        flag_id=flag.flag_id,
        knowledge_id=request.knowledge_id,
        reason=request.reason.value,
        status="pending",
        flagged_by=agent.agent_id,
        flagged_at=flag.flagged_at
    )


@router.get(
    "/flags",
    response_model=FlagListResponse,
    summary="List pending flags",
    description="List all pending moderation flags. Admin only.",
    dependencies=[Depends(require_role(AgentRole.ADMIN))]  # ISS-048: Admin enforcement at gateway
)
async def list_flags(
    limit: int = Query(default=50, ge=1, le=100),
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> FlagListResponse:
    """
    List moderation flags.

    ISS-048: Admin-only operation enforced at Gateway level via require_role.

    Args:
        limit: Maximum flags to return.
        agent: Authenticated admin agent.

    Returns:
        List of flags with counts.
    """
    repos = get_repositories()

    # Get pending flags from FlagRepository
    flags = repos["flags"].get_pending_flags(limit=limit)

    # Convert to dict for response
    flag_dicts = [f.model_dump() for f in flags]

    # Count pending
    pending_count = repos["flags"].count_pending()

    return FlagListResponse(
        flags=flag_dicts,
        total=len(flag_dicts),
        pending_count=pending_count
    )


@router.post(
    "/action",
    response_model=ModerateResponse,
    summary="Take moderation action",
    description="Take moderation action on knowledge. Admin only.",
    dependencies=[Depends(require_role(AgentRole.ADMIN))]  # ISS-048: Admin enforcement at gateway
)
async def moderate_knowledge(
    request: ModerateRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> ModerateResponse:
    """
    Take moderation action on knowledge.

    ISS-048: Admin-only operation enforced at Gateway level via require_role.

    Available actions:
    - approve: Clear flags and mark as reviewed
    - deprecate: Mark knowledge as deprecated
    - delete: Soft delete the knowledge

    Args:
        request: Moderation action request.
        agent: Authenticated admin agent.

    Returns:
        Moderation result with new status.

    Raises:
        HTTPException: If knowledge not found or action fails.
    """
    from ...db import KnowledgeUpdate

    repos = get_repositories()

    # Verify knowledge exists
    knowledge = repos["knowledge"].get_by_id(request.knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge not found")

    # Validate reason is provided for deprecate/delete
    if request.action in [ModerateAction.DEPRECATE, ModerateAction.DELETE]:
        if not request.reason:
            raise HTTPException(
                status_code=400,
                detail=f"Reason is required for {request.action.value} action"
            )

    # Apply moderation action
    new_status: KnowledgeStatus
    now = datetime.utcnow()

    # Resolve all flags for this knowledge
    flags = repos["flags"].get_flags_for_knowledge(request.knowledge_id)
    for flag in flags:
        repos["flags"].resolve_flag(
            flag.flag_id,
            reviewed_by=agent.agent_id,
            resolution=f"{request.action.value}: {request.reason or 'No reason provided'}"
        )

    if request.action == ModerateAction.APPROVE:
        # Mark as active/verified
        new_status = KnowledgeStatus.ACTIVE
        update_data = KnowledgeUpdate(status=new_status)
        repos["knowledge"].update(
            request.knowledge_id,
            update_data,
            updated_by=agent.agent_id
        )

    elif request.action == ModerateAction.DEPRECATE:
        # Mark as deprecated
        new_status = KnowledgeStatus.DEPRECATED
        update_data = KnowledgeUpdate(status=new_status)
        repos["knowledge"].update(
            request.knowledge_id,
            update_data,
            updated_by=agent.agent_id
        )

    elif request.action == ModerateAction.DELETE:
        # Soft delete
        new_status = KnowledgeStatus.DELETED
        repos["knowledge"].delete(request.knowledge_id, soft=True)

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action: {request.action}"
        )

    # Audit log
    repos["audit"].log(
        agent_id=agent.agent_id,
        action=AuditAction.KNOWLEDGE_UPDATE,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=request.knowledge_id,
        details={
            "moderation_action": request.action.value,
            "reason": request.reason,
            "new_status": new_status.value,
        },
        machine_id=agent.machine_id
    )

    logger.info(
        f"Knowledge {request.knowledge_id} moderated: {request.action.value} "
        f"by admin {agent.agent_id}"
    )

    return ModerateResponse(
        success=True,
        knowledge_id=request.knowledge_id,
        action=request.action.value,
        new_status=new_status.value,
        moderated_by=agent.agent_id,
        moderated_at=now
    )


@router.get(
    "/history",
    response_model=ModerationHistoryResponse,
    summary="Get moderation history",
    description="Get moderation action history. Admin only.",
    dependencies=[Depends(require_role(AgentRole.ADMIN))]  # ISS-048: Admin enforcement at gateway
)
async def get_moderation_history(
    knowledge_id: Optional[str] = Query(None, description="Filter by knowledge ID"),
    limit: int = Query(default=50, ge=1, le=100),
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> ModerationHistoryResponse:
    """
    Get moderation action history.

    ISS-048: Admin-only operation enforced at Gateway level via require_role.

    Returns resolved flags as moderation history.

    Args:
        knowledge_id: Optional filter by specific knowledge.
        limit: Maximum history entries to return.
        agent: Authenticated admin agent.

    Returns:
        Moderation history entries.
    """
    repos = get_repositories()

    # Get resolved flags as moderation history
    # If knowledge_id specified, get flags for that knowledge
    if knowledge_id:
        flags = repos["flags"].get_flags_for_knowledge(knowledge_id)
        # Filter to resolved only
        resolved_flags = [f for f in flags if f.status == "resolved"]
    else:
        # Get all resolved flags via direct collection query
        cursor = repos["flags"].collection.find(
            {"status": "resolved"}
        ).sort("reviewed_at", -1).limit(limit)

        resolved_flags = []
        for doc in cursor:
            doc.pop("_id", None)
            resolved_flags.append(KnowledgeFlag(**doc))

    # Convert to history format
    history = []
    for flag in resolved_flags[:limit]:
        history.append({
            "flag_id": flag.flag_id,
            "knowledge_id": flag.knowledge_id,
            "reason": flag.reason.value if hasattr(flag.reason, 'value') else flag.reason,
            "flagged_by": flag.flagged_by,
            "flagged_at": flag.flagged_at.isoformat() if flag.flagged_at else None,
            "reviewed_by": flag.reviewed_by,
            "reviewed_at": flag.reviewed_at.isoformat() if flag.reviewed_at else None,
            "resolution": flag.resolution,
        })

    return ModerationHistoryResponse(
        history=history,
        total=len(history)
    )
