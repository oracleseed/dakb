"""
DAKB Admin API Routes

REST API endpoints for admin dashboard operations:
- GET/POST/DELETE /api/v1/admin/agents - Admin agent management
- GET/PUT /api/v1/admin/config - Runtime configuration
- GET/POST /api/v1/admin/tokens - Token registry
- GET /api/v1/admin/status - Enhanced status
- GET /admin/dashboard - Admin dashboard UI

Version: 1.0
Created: 2026-01-14
Author: Claude Opus 4.5
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..db import AgentRole
from ..db.admin_schemas import (
    DEFAULT_ADMIN_AGENTS,
    DEFAULT_RUNTIME_CONFIGS,
    AdminAgentAdd,
    AdminAgentEntry,
    AdminAgentListResponse,
    AdminConfigDocument,
    RuntimeConfigDocument,
    RuntimeConfigListResponse,
    RuntimeConfigUpdate,
    TokenRefreshRequest,
    TokenRegistryCreate,
    TokenRegistryDocument,
    TokenRegistryListResponse,
    TokenRegistryResponse,
    TokenRevokeRequest,
    TokenStatus,
    hash_token,
    utcnow,
)
from ..db.collections import get_dakb_client
from ..gateway.config import get_settings
from ..gateway.middleware.auth import (
    AuthenticatedAgent,
    get_current_agent,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])

# Dashboard router (different prefix, no auth required for static HTML)
dashboard_router = APIRouter(tags=["Admin Dashboard"])


# =============================================================================
# DASHBOARD UI ENDPOINT
# =============================================================================

TEMPLATES_DIR = Path(__file__).parent / "templates"


@dashboard_router.get(
    "/admin/login",
    response_class=HTMLResponse,
    summary="Admin Login",
    description="Login page for DAKB admin dashboard.",
)
async def get_login_page() -> HTMLResponse:
    """
    Serve the admin login page.

    Returns:
        HTML login page.
    """
    template_path = TEMPLATES_DIR / "login.html"

    if not template_path.exists():
        raise HTTPException(
            status_code=500,
            detail="Login template not found"
        )

    with open(template_path) as f:
        html_content = f.read()

    return HTMLResponse(content=html_content)


@dashboard_router.get(
    "/admin/dashboard",
    response_class=HTMLResponse,
    summary="Admin Dashboard",
    description="Web-based admin dashboard for DAKB management.",
)
async def get_dashboard() -> HTMLResponse:
    """
    Serve the admin dashboard HTML page.

    Returns:
        HTML page for the admin dashboard.
    """
    template_path = TEMPLATES_DIR / "dashboard.html"

    if not template_path.exists():
        raise HTTPException(
            status_code=500,
            detail="Dashboard template not found"
        )

    with open(template_path) as f:
        html_content = f.read()

    return HTMLResponse(content=html_content)


@dashboard_router.post(
    "/admin/logout",
    summary="Admin Logout",
    description="Clear admin session.",
)
async def logout():
    """
    Clear admin session and redirect to login.

    Returns:
        Redirect response to login page.
    """
    from fastapi.responses import RedirectResponse
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("dakb_admin_session")
    return response


# =============================================================================
# COLLECTION NAMES
# =============================================================================

COLLECTION_ADMIN_CONFIG = "dakb_admin_config"
COLLECTION_RUNTIME_CONFIG = "dakb_runtime_config"
COLLECTION_TOKEN_REGISTRY = "dakb_token_registry"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_db():
    """Get database connection using settings."""
    settings = get_settings()
    client = get_dakb_client()
    return client[settings.db_name]


def get_admin_config() -> AdminConfigDocument:
    """
    Get admin config from database, or create default if not exists.

    Returns:
        AdminConfigDocument with current admin agents.
    """
    db = get_db()
    doc = db[COLLECTION_ADMIN_CONFIG].find_one({"config_type": "admin_agents"})

    if doc:
        # Convert stored agents to AdminAgentEntry objects
        agents = []
        for agent_data in doc.get("admin_agents", []):
            if isinstance(agent_data, dict):
                agents.append(AdminAgentEntry(**agent_data))
            elif isinstance(agent_data, str):
                # Legacy format - just agent_id string
                agents.append(AdminAgentEntry(
                    agent_id=agent_data,
                    added_by="system_migration"
                ))

        return AdminConfigDocument(
            config_type=doc.get("config_type", "admin_agents"),
            admin_agents=agents,
            version=doc.get("version", 1),
            created_at=doc.get("created_at", utcnow()),
            updated_at=doc.get("updated_at", utcnow()),
            updated_by=doc.get("updated_by"),
        )

    # Create default config if not exists
    default_agents = [
        AdminAgentEntry(agent_id=agent_id, added_by="system_init")
        for agent_id in DEFAULT_ADMIN_AGENTS
    ]

    config = AdminConfigDocument(admin_agents=default_agents)

    # Store in database
    db[COLLECTION_ADMIN_CONFIG].insert_one({
        "config_type": config.config_type,
        "admin_agents": [entry.model_dump() for entry in config.admin_agents],
        "version": config.version,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
        "updated_by": "system_init",
    })

    logger.info(f"Created default admin config with {len(default_agents)} agents")
    return config


def is_admin_agent(agent_id: str) -> bool:
    """
    Check if agent is an admin (using database config).

    This replaces the hardcoded ADMIN_AGENTS frozenset check.
    """
    config = get_admin_config()
    return config.is_admin(agent_id)


async def require_admin(
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> AuthenticatedAgent:
    """
    Dependency that requires admin privileges.

    Checks both:
    1. Agent role is ADMIN
    2. Agent is in the dynamic admin list
    """
    # Check role
    if agent.role != AgentRole.ADMIN:
        # Also check dynamic admin list
        if not is_admin_agent(agent.agent_id):
            logger.warning(f"Access denied for non-admin agent: {agent.agent_id}")
            raise HTTPException(
                status_code=403,
                detail="Admin privileges required"
            )

    return agent


# =============================================================================
# ADMIN AGENTS ENDPOINTS
# =============================================================================

@router.get(
    "/agents",
    response_model=AdminAgentListResponse,
    summary="List admin agents",
    description="Get list of all admin agents. Requires admin privileges.",
)
async def list_admin_agents(
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AdminAgentListResponse:
    """
    List all admin agents.

    Returns:
        List of admin agents with metadata.
    """
    config = get_admin_config()

    return AdminAgentListResponse(
        admin_agents=config.admin_agents,
        total=len(config.admin_agents),
        version=config.version,
        updated_at=config.updated_at,
    )


@router.post(
    "/agents",
    response_model=AdminAgentListResponse,
    summary="Add admin agent",
    description="Add a new admin agent. Requires admin privileges.",
)
async def add_admin_agent(
    request: AdminAgentAdd,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AdminAgentListResponse:
    """
    Add a new admin agent.

    Args:
        request: Agent to add with optional notes.
        admin: Authenticated admin making the request.

    Returns:
        Updated admin agent list.
    """
    db = get_db()
    config = get_admin_config()

    # Check if agent already exists
    if config.is_admin(request.agent_id):
        raise HTTPException(
            status_code=409,
            detail=f"Agent '{request.agent_id}' is already an admin"
        )

    # Create new entry
    new_entry = AdminAgentEntry(
        agent_id=request.agent_id,
        added_by=admin.agent_id,
        notes=request.notes,
    )

    # Update database
    result = db[COLLECTION_ADMIN_CONFIG].update_one(
        {"config_type": "admin_agents"},
        {
            "$push": {"admin_agents": new_entry.model_dump()},
            "$inc": {"version": 1},
            "$set": {
                "updated_at": utcnow(),
                "updated_by": admin.agent_id,
            }
        }
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to update admin config"
        )

    logger.info(f"Admin agent added: {request.agent_id} by {admin.agent_id}")

    # Return updated list
    updated_config = get_admin_config()
    return AdminAgentListResponse(
        admin_agents=updated_config.admin_agents,
        total=len(updated_config.admin_agents),
        version=updated_config.version,
        updated_at=updated_config.updated_at,
    )


@router.delete(
    "/agents/{agent_id}",
    response_model=AdminAgentListResponse,
    summary="Remove admin agent",
    description="Remove an admin agent. Requires admin privileges. Cannot remove yourself.",
)
async def remove_admin_agent(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AdminAgentListResponse:
    """
    Remove an admin agent.

    Args:
        agent_id: Agent to remove.
        admin: Authenticated admin making the request.

    Returns:
        Updated admin agent list.

    Raises:
        HTTPException: If trying to remove yourself or agent not found.
    """
    db = get_db()
    config = get_admin_config()

    # Prevent self-removal
    if agent_id == admin.agent_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove yourself from admin list"
        )

    # Check if agent exists
    if not config.is_admin(agent_id):
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' is not an admin"
        )

    # Prevent removing last admin
    if len(config.admin_agents) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot remove the last admin agent"
        )

    # Update database - remove agent from list
    result = db[COLLECTION_ADMIN_CONFIG].update_one(
        {"config_type": "admin_agents"},
        {
            "$pull": {"admin_agents": {"agent_id": agent_id}},
            "$inc": {"version": 1},
            "$set": {
                "updated_at": utcnow(),
                "updated_by": admin.agent_id,
            }
        }
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to update admin config"
        )

    logger.info(f"Admin agent removed: {agent_id} by {admin.agent_id}")

    # Return updated list
    updated_config = get_admin_config()
    return AdminAgentListResponse(
        admin_agents=updated_config.admin_agents,
        total=len(updated_config.admin_agents),
        version=updated_config.version,
        updated_at=updated_config.updated_at,
    )


@router.get(
    "/agents/check/{agent_id}",
    summary="Check if agent is admin",
    description="Check if a specific agent has admin privileges. Public endpoint.",
)
async def check_admin_status(agent_id: str) -> dict:
    """
    Check if an agent is an admin.

    This is a lightweight endpoint that doesn't require authentication,
    useful for frontend to check permissions.

    Args:
        agent_id: Agent to check.

    Returns:
        Dict with is_admin boolean.
    """
    is_admin = is_admin_agent(agent_id)
    return {
        "agent_id": agent_id,
        "is_admin": is_admin,
    }


# =============================================================================
# RUNTIME CONFIG ENDPOINTS
# =============================================================================

@router.get(
    "/config",
    response_model=RuntimeConfigListResponse,
    summary="List runtime configs",
    description="Get all runtime configuration settings. Requires admin privileges.",
)
async def list_runtime_configs(
    category: str | None = None,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> RuntimeConfigListResponse:
    """
    List all runtime configuration settings.

    Args:
        category: Optional filter by category.
        admin: Authenticated admin.

    Returns:
        List of runtime configs.
    """
    db = get_db()

    # Build query
    query = {}
    if category:
        query["category"] = category

    # Get configs
    cursor = db[COLLECTION_RUNTIME_CONFIG].find(query)
    configs = []

    for doc in cursor:
        configs.append(RuntimeConfigDocument(**doc))

    # If no configs exist, initialize defaults
    if not configs and not category:
        configs = await _initialize_default_configs(db, admin.agent_id)

    return RuntimeConfigListResponse(
        configs=configs,
        total=len(configs),
    )


@router.get(
    "/config/{key}",
    response_model=RuntimeConfigDocument,
    summary="Get runtime config",
    description="Get a specific runtime config by key. Requires admin privileges.",
)
async def get_runtime_config(
    key: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> RuntimeConfigDocument:
    """
    Get a specific runtime config.

    Args:
        key: Config key.
        admin: Authenticated admin.

    Returns:
        Runtime config document.
    """
    db = get_db()
    doc = db[COLLECTION_RUNTIME_CONFIG].find_one({"key": key})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Config key '{key}' not found"
        )

    return RuntimeConfigDocument(**doc)


@router.put(
    "/config/{key}",
    response_model=RuntimeConfigDocument,
    summary="Update runtime config",
    description="Update a runtime config value. Requires admin privileges.",
)
async def update_runtime_config(
    key: str,
    request: RuntimeConfigUpdate,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> RuntimeConfigDocument:
    """
    Update a runtime config value.

    Args:
        key: Config key.
        request: New value.
        admin: Authenticated admin.

    Returns:
        Updated config document.
    """
    db = get_db()

    # Get existing config
    doc = db[COLLECTION_RUNTIME_CONFIG].find_one({"key": key})
    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Config key '{key}' not found"
        )

    config = RuntimeConfigDocument(**doc)

    # Validate value range
    if config.min_value is not None and request.value < config.min_value:
        raise HTTPException(
            status_code=400,
            detail=f"Value must be >= {config.min_value}"
        )

    if config.max_value is not None and request.value > config.max_value:
        raise HTTPException(
            status_code=400,
            detail=f"Value must be <= {config.max_value}"
        )

    if config.allowed_values and request.value not in config.allowed_values:
        raise HTTPException(
            status_code=400,
            detail=f"Value must be one of: {config.allowed_values}"
        )

    # Update database
    result = db[COLLECTION_RUNTIME_CONFIG].update_one(
        {"key": key},
        {
            "$set": {
                "value": request.value,
                "updated_at": utcnow(),
                "updated_by": admin.agent_id,
            }
        }
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to update config"
        )

    logger.info(f"Config updated: {key}={request.value} by {admin.agent_id}")

    # Return updated config
    updated_doc = db[COLLECTION_RUNTIME_CONFIG].find_one({"key": key})
    return RuntimeConfigDocument(**updated_doc)


async def _initialize_default_configs(db, admin_id: str) -> list[RuntimeConfigDocument]:
    """Initialize default runtime configs in database."""
    configs = []

    for cfg_data in DEFAULT_RUNTIME_CONFIGS:
        doc = {
            **cfg_data,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "updated_by": admin_id,
        }
        db[COLLECTION_RUNTIME_CONFIG].insert_one(doc)
        configs.append(RuntimeConfigDocument(**doc))

    logger.info(f"Initialized {len(configs)} default runtime configs")
    return configs


# =============================================================================
# ENHANCED STATUS ENDPOINT
# =============================================================================

class DetailedStatusResponse(BaseModel):
    """Enhanced status response with detailed metrics."""
    status: str = Field(default="ok")
    uptime_seconds: float | None = None
    services: dict = Field(default_factory=dict)
    agents: dict = Field(default_factory=dict)
    knowledge: dict = Field(default_factory=dict)
    messages: dict = Field(default_factory=dict)
    admin_count: int = 0
    timestamp: datetime = Field(default_factory=utcnow)


@router.get(
    "/status",
    response_model=DetailedStatusResponse,
    summary="Detailed system status",
    description="Get comprehensive system status. Requires admin privileges.",
)
async def get_detailed_status(
    admin: AuthenticatedAgent = Depends(require_admin)
) -> DetailedStatusResponse:
    """
    Get detailed system status for admin dashboard.

    Returns comprehensive metrics including:
    - Service health
    - Agent statistics
    - Knowledge base stats
    - Message queue stats
    """
    db = get_db()

    # Get admin count
    admin_config = get_admin_config()

    # Count agents
    total_agents = db["dakb_agents"].count_documents({})
    active_agents = db["dakb_agents"].count_documents({"status": "active"})

    # Count knowledge
    total_knowledge = db["dakb_knowledge"].count_documents({})

    # Get knowledge by category
    pipeline = [
        {"$group": {"_id": "$category", "count": {"$sum": 1}}}
    ]
    category_counts = {}
    for doc in db["dakb_knowledge"].aggregate(pipeline):
        category_counts[doc["_id"]] = doc["count"]

    # Count messages
    total_messages = db["dakb_messages"].count_documents({})
    pending_messages = db["dakb_messages"].count_documents({"status": "pending"})

    return DetailedStatusResponse(
        status="ok",
        services={
            "gateway": {"status": "running"},
            "mongodb": {"status": "connected"},
        },
        agents={
            "total": total_agents,
            "active": active_agents,
        },
        knowledge={
            "total": total_knowledge,
            "by_category": category_counts,
        },
        messages={
            "total": total_messages,
            "pending": pending_messages,
        },
        admin_count=len(admin_config.admin_agents),
    )


# =============================================================================
# TOKEN REGISTRY ENDPOINTS
# =============================================================================

@router.get(
    "/tokens",
    response_model=TokenRegistryListResponse,
    summary="List registered tokens",
    description="Get list of all registered tokens with metadata. Requires admin privileges.",
)
async def list_tokens(
    status: str | None = None,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> TokenRegistryListResponse:
    """
    List all registered tokens.

    Args:
        status: Optional filter by token status.
        admin: Authenticated admin.

    Returns:
        List of tokens with counts by status.
    """
    db = get_db()

    # Build query
    query = {}
    if status:
        query["status"] = status

    # Get tokens
    cursor = db[COLLECTION_TOKEN_REGISTRY].find(query)
    tokens = []
    active_count = 0
    expired_count = 0
    revoked_count = 0

    for doc in cursor:
        token_doc = TokenRegistryDocument(**doc)
        tokens.append(token_doc)

        if token_doc.status == TokenStatus.ACTIVE:
            # Check if actually expired
            if token_doc.expires_at <= utcnow():
                expired_count += 1
            else:
                active_count += 1
        elif token_doc.status == TokenStatus.EXPIRED:
            expired_count += 1
        elif token_doc.status == TokenStatus.REVOKED:
            revoked_count += 1

    return TokenRegistryListResponse(
        tokens=tokens,
        total=len(tokens),
        active_count=active_count,
        expired_count=expired_count,
        revoked_count=revoked_count,
    )


@router.get(
    "/tokens/{agent_id}",
    response_model=TokenRegistryResponse,
    summary="Get token info",
    description="Get token metadata for a specific agent. Requires admin privileges.",
)
async def get_token_info(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> TokenRegistryResponse:
    """
    Get token information for an agent.

    Args:
        agent_id: Agent identifier.
        admin: Authenticated admin.

    Returns:
        Token metadata.
    """
    db = get_db()
    doc = db[COLLECTION_TOKEN_REGISTRY].find_one({"agent_id": agent_id})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"No token registered for agent '{agent_id}'"
        )

    token_doc = TokenRegistryDocument(**doc)

    # Calculate days until expiry (handle timezone-naive datetimes from MongoDB)
    now = utcnow()
    expires_at = token_doc.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at > now:
        days_until_expiry = (expires_at - now).days
    else:
        days_until_expiry = 0

    return TokenRegistryResponse(
        agent_id=token_doc.agent_id,
        status=token_doc.status,
        created_at=token_doc.created_at,
        expires_at=token_doc.expires_at,
        last_used_at=token_doc.last_used_at,
        use_count=token_doc.use_count,
        is_valid=token_doc.is_valid(),
        days_until_expiry=days_until_expiry,
        agent_type=token_doc.agent_type,
        role=token_doc.role,
        access_levels=token_doc.access_levels,
    )


@router.post(
    "/tokens/{agent_id}/refresh",
    response_model=TokenRegistryResponse,
    summary="Refresh token expiry",
    description="Extend token expiration time. Requires admin privileges.",
)
async def refresh_token(
    agent_id: str,
    request: TokenRefreshRequest,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> TokenRegistryResponse:
    """
    Refresh/extend token expiration.

    Args:
        agent_id: Agent identifier.
        request: Refresh request with extension hours.
        admin: Authenticated admin.

    Returns:
        Updated token info.
    """
    db = get_db()
    doc = db[COLLECTION_TOKEN_REGISTRY].find_one({"agent_id": agent_id})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"No token registered for agent '{agent_id}'"
        )

    token_doc = TokenRegistryDocument(**doc)

    if token_doc.status == TokenStatus.REVOKED:
        raise HTTPException(
            status_code=400,
            detail="Cannot refresh a revoked token"
        )

    # Calculate new expiry
    from datetime import timedelta
    new_expires_at = utcnow() + timedelta(hours=request.extend_hours)

    # Update database
    result = db[COLLECTION_TOKEN_REGISTRY].update_one(
        {"agent_id": agent_id},
        {
            "$set": {
                "expires_at": new_expires_at,
                "status": TokenStatus.ACTIVE,
                "updated_at": utcnow(),
            }
        }
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to refresh token"
        )

    logger.info(f"Token refreshed for {agent_id} by {admin.agent_id}, new expiry: {new_expires_at}")

    # Return updated info
    days_until_expiry = (new_expires_at - utcnow()).days

    return TokenRegistryResponse(
        agent_id=agent_id,
        status=TokenStatus.ACTIVE,
        created_at=token_doc.created_at,
        expires_at=new_expires_at,
        last_used_at=token_doc.last_used_at,
        use_count=token_doc.use_count,
        is_valid=True,
        days_until_expiry=days_until_expiry,
        agent_type=token_doc.agent_type,
        role=token_doc.role,
        access_levels=token_doc.access_levels,
    )


@router.post(
    "/tokens/{agent_id}/revoke",
    response_model=TokenRegistryResponse,
    summary="Revoke token",
    description="Revoke an agent's token. Requires admin privileges.",
)
async def revoke_token(
    agent_id: str,
    request: TokenRevokeRequest,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> TokenRegistryResponse:
    """
    Revoke an agent's token.

    Args:
        agent_id: Agent identifier.
        request: Revocation request with optional reason.
        admin: Authenticated admin.

    Returns:
        Updated token info.
    """
    db = get_db()
    doc = db[COLLECTION_TOKEN_REGISTRY].find_one({"agent_id": agent_id})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"No token registered for agent '{agent_id}'"
        )

    token_doc = TokenRegistryDocument(**doc)

    if token_doc.status == TokenStatus.REVOKED:
        raise HTTPException(
            status_code=400,
            detail="Token is already revoked"
        )

    # Prevent revoking your own token
    if agent_id == admin.agent_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot revoke your own token"
        )

    # Update database
    result = db[COLLECTION_TOKEN_REGISTRY].update_one(
        {"agent_id": agent_id},
        {
            "$set": {
                "status": TokenStatus.REVOKED,
                "revoked_at": utcnow(),
                "revoked_by": admin.agent_id,
                "revocation_reason": request.reason,
            }
        }
    )

    if result.modified_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to revoke token"
        )

    logger.info(f"Token revoked for {agent_id} by {admin.agent_id}, reason: {request.reason}")

    return TokenRegistryResponse(
        agent_id=agent_id,
        status=TokenStatus.REVOKED,
        created_at=token_doc.created_at,
        expires_at=token_doc.expires_at,
        last_used_at=token_doc.last_used_at,
        use_count=token_doc.use_count,
        is_valid=False,
        days_until_expiry=0,
        agent_type=token_doc.agent_type,
        role=token_doc.role,
        access_levels=token_doc.access_levels,
    )


@router.post(
    "/tokens",
    response_model=TokenRegistryResponse,
    summary="Register token",
    description="Register a new token in the registry. Requires admin privileges.",
)
async def register_token(
    request: TokenRegistryCreate,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> TokenRegistryResponse:
    """
    Register a new token in the registry.

    Note: This only registers token metadata. The actual token
    must be generated separately (e.g., via /api/v1/token endpoint).

    Args:
        request: Token registration data.
        admin: Authenticated admin.

    Returns:
        Registered token info.
    """
    db = get_db()

    # Check if token already registered for agent
    existing = db[COLLECTION_TOKEN_REGISTRY].find_one({"agent_id": request.agent_id})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Token already registered for agent '{request.agent_id}'"
        )

    # Create token registry document
    token_doc = TokenRegistryDocument(
        agent_id=request.agent_id,
        token_fingerprint=request.token_fingerprint,
        created_by=admin.agent_id,
        expires_at=request.expires_at,
        agent_type=request.agent_type,
        role=request.role,
        access_levels=request.access_levels,
        notes=request.notes,
    )

    # Insert into database
    db[COLLECTION_TOKEN_REGISTRY].insert_one(token_doc.model_dump())

    logger.info(f"Token registered for {request.agent_id} by {admin.agent_id}")

    # Calculate days until expiry
    days_until_expiry = (token_doc.expires_at - utcnow()).days

    return TokenRegistryResponse(
        agent_id=token_doc.agent_id,
        status=token_doc.status,
        created_at=token_doc.created_at,
        expires_at=token_doc.expires_at,
        last_used_at=token_doc.last_used_at,
        use_count=token_doc.use_count,
        is_valid=token_doc.is_valid(),
        days_until_expiry=days_until_expiry,
        agent_type=token_doc.agent_type,
        role=token_doc.role,
        access_levels=token_doc.access_levels,
    )


# =============================================================================
# AGENT MANAGEMENT ENDPOINTS (All Registered Agents)
# =============================================================================

class AllAgentResponse(BaseModel):
    """Response model for a single agent."""
    agent_id: str
    display_name: str
    agent_type: str
    machine_id: str
    role: str
    status: str
    capabilities: list[str] = Field(default_factory=list)
    specializations: list[str] = Field(default_factory=list)
    knowledge_contributed: int = 0
    messages_sent: int = 0
    messages_received: int = 0
    last_seen: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AllAgentListResponse(BaseModel):
    """Response model for list of all agents."""
    agents: list[AllAgentResponse]
    total: int
    active_count: int
    suspended_count: int
    offline_count: int


class AgentUpdateRequest(BaseModel):
    """Request model for updating an agent."""
    display_name: str | None = Field(None, max_length=100)
    role: str | None = Field(None, description="Agent role: admin, developer, researcher, viewer")
    status: str | None = Field(None, description="Agent status: active, suspended, offline")
    capabilities: list[str] | None = None
    specializations: list[str] | None = None
    notes: str | None = Field(None, max_length=500)


class AgentActionResponse(BaseModel):
    """Response model for agent actions."""
    success: bool
    agent_id: str
    action: str
    message: str


class TokenInfoResponse(BaseModel):
    """Response model for token info endpoint."""
    agent_id: str
    token_status: str  # active, expired, revoked, unknown
    expires_at: datetime | None = None
    days_until_expiry: int = 0
    role: str | None = None
    access_levels: list[str] = Field(default_factory=list)
    last_used_at: datetime | None = None
    use_count: int = 0


class TokenRegenerateRequest(BaseModel):
    """Request model for token regeneration."""
    expires_in_hours: int = Field(
        default=8760, ge=1, le=87600,
        description="Hours until expiry (default 1 year)"
    )
    role: str | None = Field(None, description="New role (optional, keeps existing)")
    access_levels: list[str] | None = Field(None, description="New access levels (optional)")


class TokenRegenerateResponse(BaseModel):
    """Response model for token regeneration."""
    success: bool
    agent_id: str
    token: str  # Only shown once!
    expires_at: datetime
    message: str = "Save this token securely - it will not be shown again"


COLLECTION_AGENTS = "dakb_agents"


@router.get(
    "/all-agents",
    response_model=AllAgentListResponse,
    summary="List all registered agents",
    description="Get list of all registered agents in the system. Requires admin privileges.",
)
async def list_all_agents(
    status: str | None = None,
    role: str | None = None,
    agent_type: str | None = None,
    skip: int = 0,
    limit: int = 50,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AllAgentListResponse:
    """
    List all registered agents with optional filtering.

    Args:
        status: Filter by agent status (active, suspended, offline).
        role: Filter by role (admin, developer, researcher, viewer).
        agent_type: Filter by agent type (claude, gpt, gemini, etc.).
        skip: Number of records to skip (for pagination).
        limit: Maximum records to return.
        admin: Authenticated admin.

    Returns:
        List of all agents with statistics.
    """
    db = get_db()

    # Build query
    query = {}
    if status:
        query["status"] = status
    if role:
        query["role"] = role
    if agent_type:
        query["agent_type"] = agent_type

    # Get agents with pagination
    cursor = db[COLLECTION_AGENTS].find(query).skip(skip).limit(limit).sort("created_at", -1)
    agents = []
    active_count = 0
    suspended_count = 0
    offline_count = 0

    for doc in cursor:
        agent_status = doc.get("status", "offline")
        if agent_status == "active":
            active_count += 1
        elif agent_status == "suspended":
            suspended_count += 1
        else:
            offline_count += 1

        agents.append(AllAgentResponse(
            agent_id=doc.get("agent_id", ""),
            display_name=doc.get("display_name", doc.get("agent_id", "")),
            agent_type=doc.get("agent_type", "unknown"),
            machine_id=doc.get("machine_id", ""),
            role=doc.get("role", "developer"),
            status=agent_status,
            capabilities=doc.get("capabilities", []),
            specializations=doc.get("specializations", []),
            knowledge_contributed=doc.get("knowledge_contributed", 0),
            messages_sent=doc.get("messages_sent", 0),
            messages_received=doc.get("messages_received", 0),
            last_seen=doc.get("last_seen"),
            created_at=doc.get("created_at", utcnow()),
            updated_at=doc.get("updated_at", utcnow()),
        ))

    # Get total count
    total = db[COLLECTION_AGENTS].count_documents(query)

    return AllAgentListResponse(
        agents=agents,
        total=total,
        active_count=active_count,
        suspended_count=suspended_count,
        offline_count=offline_count,
    )


@router.get(
    "/all-agents/{agent_id}",
    response_model=AllAgentResponse,
    summary="Get agent details",
    description="Get detailed information about a specific agent. Requires admin privileges.",
)
async def get_agent_details(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AllAgentResponse:
    """
    Get detailed information about a specific agent.

    Args:
        agent_id: Agent identifier.
        admin: Authenticated admin.

    Returns:
        Agent details.
    """
    db = get_db()
    doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )

    return AllAgentResponse(
        agent_id=doc.get("agent_id", ""),
        display_name=doc.get("display_name", doc.get("agent_id", "")),
        agent_type=doc.get("agent_type", "unknown"),
        machine_id=doc.get("machine_id", ""),
        role=doc.get("role", "developer"),
        status=doc.get("status", "offline"),
        capabilities=doc.get("capabilities", []),
        specializations=doc.get("specializations", []),
        knowledge_contributed=doc.get("knowledge_contributed", 0),
        messages_sent=doc.get("messages_sent", 0),
        messages_received=doc.get("messages_received", 0),
        last_seen=doc.get("last_seen"),
        created_at=doc.get("created_at", utcnow()),
        updated_at=doc.get("updated_at", utcnow()),
    )


@router.put(
    "/all-agents/{agent_id}",
    response_model=AllAgentResponse,
    summary="Update agent",
    description="Update agent properties. Requires admin privileges.",
)
async def update_agent(
    agent_id: str,
    request: AgentUpdateRequest,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AllAgentResponse:
    """
    Update agent properties.

    Args:
        agent_id: Agent identifier.
        request: Update data.
        admin: Authenticated admin.

    Returns:
        Updated agent details.
    """
    db = get_db()
    doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )

    # Build update document
    update_doc = {"updated_at": utcnow()}

    if request.display_name is not None:
        update_doc["display_name"] = request.display_name
    if request.role is not None:
        # Validate role
        valid_roles = [
            "admin", "developer", "researcher", "viewer",
            "specialist", "coordinator", "analyst", "observer"
        ]
        if request.role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role. Must be one of: {valid_roles}"
            )
        update_doc["role"] = request.role
    if request.status is not None:
        # Validate status
        valid_statuses = ["active", "idle", "offline", "suspended"]
        if request.status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {valid_statuses}"
            )
        update_doc["status"] = request.status
    if request.capabilities is not None:
        update_doc["capabilities"] = request.capabilities
    if request.specializations is not None:
        update_doc["specializations"] = request.specializations

    # Update database
    result = db[COLLECTION_AGENTS].update_one(
        {"agent_id": agent_id},
        {"$set": update_doc}
    )

    if result.modified_count == 0 and len(update_doc) > 1:
        logger.warning(f"No changes made to agent {agent_id}")

    logger.info(f"Agent updated: {agent_id} by {admin.agent_id}")

    # Return updated agent
    updated_doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})
    return AllAgentResponse(
        agent_id=updated_doc.get("agent_id", ""),
        display_name=updated_doc.get("display_name", updated_doc.get("agent_id", "")),
        agent_type=updated_doc.get("agent_type", "unknown"),
        machine_id=updated_doc.get("machine_id", ""),
        role=updated_doc.get("role", "developer"),
        status=updated_doc.get("status", "offline"),
        capabilities=updated_doc.get("capabilities", []),
        specializations=updated_doc.get("specializations", []),
        knowledge_contributed=updated_doc.get("knowledge_contributed", 0),
        messages_sent=updated_doc.get("messages_sent", 0),
        messages_received=updated_doc.get("messages_received", 0),
        last_seen=updated_doc.get("last_seen"),
        created_at=updated_doc.get("created_at", utcnow()),
        updated_at=updated_doc.get("updated_at", utcnow()),
    )


@router.post(
    "/all-agents/{agent_id}/suspend",
    response_model=AgentActionResponse,
    summary="Suspend agent",
    description="Suspend an agent, preventing them from using the system. Requires admin privileges.",
)
async def suspend_agent(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AgentActionResponse:
    """
    Suspend an agent.

    Args:
        agent_id: Agent identifier.
        admin: Authenticated admin.

    Returns:
        Action result.
    """
    db = get_db()
    doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )

    # Prevent self-suspension
    if agent_id == admin.agent_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot suspend yourself"
        )

    current_status = doc.get("status", "offline")
    if current_status == "suspended":
        raise HTTPException(
            status_code=400,
            detail="Agent is already suspended"
        )

    # Update status
    result = db[COLLECTION_AGENTS].update_one(
        {"agent_id": agent_id},
        {
            "$set": {
                "status": "suspended",
                "suspended_at": utcnow(),
                "suspended_by": admin.agent_id,
                "updated_at": utcnow(),
            }
        }
    )

    logger.info(f"Agent suspended: {agent_id} by {admin.agent_id}")

    return AgentActionResponse(
        success=True,
        agent_id=agent_id,
        action="suspend",
        message=f"Agent '{agent_id}' has been suspended"
    )


@router.post(
    "/all-agents/{agent_id}/activate",
    response_model=AgentActionResponse,
    summary="Activate agent",
    description="Reactivate a suspended agent. Requires admin privileges.",
)
async def activate_agent(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AgentActionResponse:
    """
    Reactivate a suspended agent.

    Args:
        agent_id: Agent identifier.
        admin: Authenticated admin.

    Returns:
        Action result.
    """
    db = get_db()
    doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )

    current_status = doc.get("status", "offline")
    if current_status == "active":
        raise HTTPException(
            status_code=400,
            detail="Agent is already active"
        )

    # Update status
    result = db[COLLECTION_AGENTS].update_one(
        {"agent_id": agent_id},
        {
            "$set": {
                "status": "active",
                "activated_at": utcnow(),
                "activated_by": admin.agent_id,
                "updated_at": utcnow(),
            },
            "$unset": {
                "suspended_at": "",
                "suspended_by": "",
            }
        }
    )

    logger.info(f"Agent activated: {agent_id} by {admin.agent_id}")

    return AgentActionResponse(
        success=True,
        agent_id=agent_id,
        action="activate",
        message=f"Agent '{agent_id}' has been activated"
    )


@router.delete(
    "/all-agents/{agent_id}",
    response_model=AgentActionResponse,
    summary="Delete agent",
    description="Permanently delete an agent from the system. Requires admin privileges. This action cannot be undone.",
)
async def delete_agent(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AgentActionResponse:
    """
    Delete an agent from the system.

    Args:
        agent_id: Agent identifier.
        admin: Authenticated admin.

    Returns:
        Action result.
    """
    db = get_db()
    doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})

    if not doc:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found"
        )

    # Prevent self-deletion
    if agent_id == admin.agent_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete yourself"
        )

    # Delete the agent
    result = db[COLLECTION_AGENTS].delete_one({"agent_id": agent_id})

    if result.deleted_count == 0:
        raise HTTPException(
            status_code=500,
            detail="Failed to delete agent"
        )

    logger.info(f"Agent deleted: {agent_id} by {admin.agent_id}")

    return AgentActionResponse(
        success=True,
        agent_id=agent_id,
        action="delete",
        message=f"Agent '{agent_id}' has been permanently deleted"
    )


# =============================================================================
# TOKEN MANAGEMENT ENDPOINTS (for All Agents)
# =============================================================================

@router.get(
    "/all-agents/{agent_id}/token-info",
    response_model=TokenInfoResponse,
    summary="Get agent token info",
    description="Get token status and expiration info for an agent. Requires admin privileges.",
)
async def get_agent_token_info(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> TokenInfoResponse:
    """Get token information for any registered agent."""
    db = get_db()

    # Check agent exists
    agent_doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})
    if not agent_doc:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    # Get token info from registry
    token_doc = db[COLLECTION_TOKEN_REGISTRY].find_one({"agent_id": agent_id})

    if not token_doc:
        return TokenInfoResponse(
            agent_id=agent_id,
            token_status="unknown",
            days_until_expiry=0,
        )

    token = TokenRegistryDocument(**token_doc)

    # Calculate status
    now = utcnow()
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if token.status == TokenStatus.REVOKED:
        status = "revoked"
        days_until_expiry = 0
    elif expires_at <= now:
        status = "expired"
        days_until_expiry = 0
    else:
        status = "active"
        days_until_expiry = (expires_at - now).days

    return TokenInfoResponse(
        agent_id=agent_id,
        token_status=status,
        expires_at=token.expires_at,
        days_until_expiry=days_until_expiry,
        role=token.role,
        access_levels=token.access_levels,
        last_used_at=token.last_used_at,
        use_count=token.use_count,
    )


@router.post(
    "/all-agents/{agent_id}/regenerate-token",
    response_model=TokenRegenerateResponse,
    summary="Regenerate agent token",
    description="Generate a new authentication token for an agent. Returns token only once. Requires admin privileges.",
)
async def regenerate_agent_token(
    agent_id: str,
    request: TokenRegenerateRequest = TokenRegenerateRequest(),
    admin: AuthenticatedAgent = Depends(require_admin)
) -> TokenRegenerateResponse:
    """Regenerate token for an agent with new expiry."""
    db = get_db()

    # Check agent exists
    agent_doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})
    if not agent_doc:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    # Generate new token
    new_token = secrets.token_urlsafe(32)
    new_fingerprint = hash_token(new_token)
    new_expires = utcnow() + timedelta(hours=request.expires_in_hours)

    # Prepare update
    update_data = {
        "token_fingerprint": new_fingerprint,
        "expires_at": new_expires,
        "status": TokenStatus.ACTIVE,
        "updated_at": utcnow(),
    }

    # Optionally update role and access_levels
    if request.role is not None:
        update_data["role"] = request.role
    if request.access_levels is not None:
        update_data["access_levels"] = request.access_levels

    # Update or create token registry entry
    db[COLLECTION_TOKEN_REGISTRY].update_one(
        {"agent_id": agent_id},
        {"$set": update_data},
        upsert=True
    )

    logger.info(f"Token regenerated for {agent_id} by {admin.agent_id}, expires: {new_expires}")

    return TokenRegenerateResponse(
        success=True,
        agent_id=agent_id,
        token=new_token,
        expires_at=new_expires,
    )


@router.post(
    "/all-agents/{agent_id}/expire-token",
    response_model=AgentActionResponse,
    summary="Expire agent token",
    description="Immediately expire an agent's token (soft revoke). Requires admin privileges.",
)
async def expire_agent_token(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AgentActionResponse:
    """Expire token immediately."""
    db = get_db()

    # Check token exists
    token_doc = db[COLLECTION_TOKEN_REGISTRY].find_one({"agent_id": agent_id})
    if not token_doc:
        raise HTTPException(status_code=404, detail=f"No token for agent '{agent_id}'")

    # Prevent self-expiration
    if agent_id == admin.agent_id:
        raise HTTPException(status_code=400, detail="Cannot expire your own token")

    # Update token status
    db[COLLECTION_TOKEN_REGISTRY].update_one(
        {"agent_id": agent_id},
        {
            "$set": {
                "status": TokenStatus.EXPIRED,
                "expires_at": utcnow(),
                "updated_at": utcnow(),
                "revoked_by": admin.agent_id,
                "revocation_reason": "Manually expired by admin",
            }
        }
    )

    logger.info(f"Token expired for {agent_id} by {admin.agent_id}")

    return AgentActionResponse(
        success=True,
        agent_id=agent_id,
        action="expire_token",
        message=f"Token for agent '{agent_id}' has been expired"
    )


# =============================================================================
# KNOWLEDGE BASE MANAGEMENT ENDPOINTS
# =============================================================================

COLLECTION_KNOWLEDGE = "dakb_knowledge"


class KnowledgeEntry(BaseModel):
    """Knowledge entry data model."""
    knowledge_id: str
    title: str
    content: str
    category: str
    status: str
    content_type: str
    tags: list[str] = Field(default_factory=list)
    source: dict = Field(default_factory=dict)
    votes: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class KnowledgeListResponse(BaseModel):
    """Response for knowledge list."""
    items: list[dict]
    total: int
    page: int
    page_size: int
    has_more: bool


class KnowledgeStatsResponse(BaseModel):
    """Response for knowledge statistics."""
    total_entries: int
    categories: dict
    content_types: dict
    statuses: dict
    agents: dict
    recent_24h: int


class KnowledgeUpdateRequest(BaseModel):
    """Request to update knowledge entry."""
    title: str | None = None
    content: str | None = None
    category: str | None = None
    status: str | None = None
    tags: list[str] | None = None


class KnowledgeDeleteResponse(BaseModel):
    """Response for knowledge deletion."""
    success: bool
    knowledge_id: str
    action: str  # soft_delete or hard_delete
    message: str


class RedundancyCheckResponse(BaseModel):
    """Response for redundancy check."""
    knowledge_id: str
    title: str
    similar_entries: list[dict]
    highest_similarity: float


@router.get(
    "/knowledge",
    response_model=KnowledgeListResponse,
    summary="List knowledge entries",
    description="List knowledge with search, filter, and pagination. Admin only.",
)
async def list_knowledge(
    page: int = 1,
    page_size: int = 20,
    category: str | None = None,
    status: str | None = None,
    agent_id: str | None = None,
    search: str | None = None,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> KnowledgeListResponse:
    """List knowledge entries with filtering and search."""
    import re
    db = get_db()

    query: dict = {}

    # Apply filters
    if category:
        query["category"] = category
    if status:
        query["status"] = status
    if agent_id:
        query["source.agent_id"] = agent_id

    # Apply search (MongoDB regex on title, content, tags, ID)
    if search:
        search_regex = re.compile(re.escape(search), re.IGNORECASE)
        query["$or"] = [
            {"title": {"$regex": search_regex}},
            {"content": {"$regex": search_regex}},
            {"tags": {"$regex": search_regex}},
            {"knowledge_id": {"$regex": search_regex}},
        ]

    # Pagination
    skip = (page - 1) * page_size
    cursor = db[COLLECTION_KNOWLEDGE].find(query).skip(skip).limit(page_size + 1).sort("created_at", -1)

    items = list(cursor)
    has_more = len(items) > page_size
    items = items[:page_size]

    # Convert ObjectId to string
    for item in items:
        item["_id"] = str(item["_id"])

    total = db[COLLECTION_KNOWLEDGE].count_documents(query)

    return KnowledgeListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=has_more,
    )


@router.get(
    "/knowledge-stats",
    response_model=KnowledgeStatsResponse,
    summary="Get knowledge statistics",
    description="Get knowledge base statistics. Admin only.",
)
async def get_knowledge_stats(
    admin: AuthenticatedAgent = Depends(require_admin)
) -> KnowledgeStatsResponse:
    """Get knowledge base statistics."""
    db = get_db()

    total = db[COLLECTION_KNOWLEDGE].count_documents({})

    # By category
    categories = {}
    for doc in db[COLLECTION_KNOWLEDGE].aggregate([
        {"$group": {"_id": "$category", "count": {"$sum": 1}}}
    ]):
        categories[doc["_id"] or "unknown"] = doc["count"]

    # By content_type
    content_types = {}
    for doc in db[COLLECTION_KNOWLEDGE].aggregate([
        {"$group": {"_id": "$content_type", "count": {"$sum": 1}}}
    ]):
        content_types[doc["_id"] or "unknown"] = doc["count"]

    # By status
    statuses = {}
    for doc in db[COLLECTION_KNOWLEDGE].aggregate([
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]):
        statuses[doc["_id"] or "unknown"] = doc["count"]

    # By agent
    agents = {}
    for doc in db[COLLECTION_KNOWLEDGE].aggregate([
        {"$group": {"_id": "$source.agent_id", "count": {"$sum": 1}}}
    ]):
        agents[doc["_id"] or "unknown"] = doc["count"]

    # Recent (last 24h)
    cutoff = utcnow() - timedelta(hours=24)
    recent_24h = db[COLLECTION_KNOWLEDGE].count_documents({"created_at": {"$gte": cutoff}})

    return KnowledgeStatsResponse(
        total_entries=total,
        categories=categories,
        content_types=content_types,
        statuses=statuses,
        agents=agents,
        recent_24h=recent_24h,
    )


@router.get(
    "/knowledge/{knowledge_id}",
    summary="Get single knowledge entry",
    description="Get full knowledge entry by ID. Admin only.",
)
async def get_knowledge_entry(
    knowledge_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> dict:
    """Get single knowledge entry."""
    db = get_db()
    doc = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})

    if not doc:
        raise HTTPException(status_code=404, detail=f"Knowledge '{knowledge_id}' not found")

    doc["_id"] = str(doc["_id"])
    return doc


@router.put(
    "/knowledge/{knowledge_id}",
    summary="Update knowledge entry",
    description="Update knowledge entry fields. Admin only.",
)
async def update_knowledge_entry(
    knowledge_id: str,
    request: KnowledgeUpdateRequest,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> dict:
    """Update knowledge entry."""
    db = get_db()
    doc = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})

    if not doc:
        raise HTTPException(status_code=404, detail=f"Knowledge '{knowledge_id}' not found")

    update_doc: dict = {"updated_at": utcnow()}

    if request.title is not None:
        update_doc["title"] = request.title
    if request.content is not None:
        update_doc["content"] = request.content
    if request.category is not None:
        update_doc["category"] = request.category
    if request.status is not None:
        update_doc["status"] = request.status
    if request.tags is not None:
        update_doc["tags"] = request.tags

    db[COLLECTION_KNOWLEDGE].update_one(
        {"knowledge_id": knowledge_id},
        {"$set": update_doc}
    )

    logger.info(f"Knowledge updated: {knowledge_id} by {admin.agent_id}")

    updated = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})
    if updated:
        updated["_id"] = str(updated["_id"])
    return updated or {}


@router.delete(
    "/knowledge/{knowledge_id}",
    response_model=KnowledgeDeleteResponse,
    summary="Delete knowledge entry",
    description="Soft or hard delete knowledge entry. Admin only.",
)
async def delete_knowledge_entry(
    knowledge_id: str,
    hard_delete: bool = False,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> KnowledgeDeleteResponse:
    """Delete knowledge entry (soft or hard)."""
    db = get_db()
    doc = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})

    if not doc:
        raise HTTPException(status_code=404, detail=f"Knowledge '{knowledge_id}' not found")

    if hard_delete:
        db[COLLECTION_KNOWLEDGE].delete_one({"knowledge_id": knowledge_id})
        action = "hard_delete"
        message = f"Knowledge '{knowledge_id}' permanently deleted"
    else:
        db[COLLECTION_KNOWLEDGE].update_one(
            {"knowledge_id": knowledge_id},
            {
                "$set": {
                    "status": "deleted",
                    "deleted_at": utcnow(),
                    "deleted_by": admin.agent_id,
                }
            }
        )
        action = "soft_delete"
        message = f"Knowledge '{knowledge_id}' marked as deleted"

    logger.info(f"Knowledge {action}: {knowledge_id} by {admin.agent_id}")

    return KnowledgeDeleteResponse(
        success=True,
        knowledge_id=knowledge_id,
        action=action,
        message=message,
    )


@router.get(
    "/knowledge/{knowledge_id}/redundancy",
    response_model=RedundancyCheckResponse,
    summary="Check knowledge redundancy",
    description="Find similar knowledge entries. Admin only.",
)
async def check_knowledge_redundancy(
    knowledge_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> RedundancyCheckResponse:
    """Check for similar/redundant knowledge entries."""
    import re
    db = get_db()

    doc = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Knowledge '{knowledge_id}' not found")

    title = doc.get("title", "")
    similar: list[dict] = []
    highest_similarity = 0.0

    # Simple text-based similarity check using title prefix
    if title and len(title) >= 10:
        search_prefix = title[:20]
        search_regex = re.compile(re.escape(search_prefix), re.IGNORECASE)

        cursor = db[COLLECTION_KNOWLEDGE].find({
            "knowledge_id": {"$ne": knowledge_id},
            "title": {"$regex": search_regex}
        }).limit(10)

        for match in cursor:
            # Simple similarity score based on title match
            similarity = 0.5 + (0.3 if match.get("category") == doc.get("category") else 0)
            similar.append({
                "knowledge_id": match["knowledge_id"],
                "title": match.get("title"),
                "category": match.get("category"),
                "similarity": similarity,
            })
            highest_similarity = max(highest_similarity, similarity)

    return RedundancyCheckResponse(
        knowledge_id=knowledge_id,
        title=title,
        similar_entries=similar,
        highest_similarity=highest_similarity,
    )
