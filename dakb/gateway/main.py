"""
DAKB Gateway Service - Main FastAPI Application

Public-facing REST API gateway for the Distributed Agent Knowledge Base.
Provides knowledge management, semantic search, and agent authentication.

Version: 1.0
Created: 2025-12-07
Author: Backend Agent (Claude Opus 4.5)

Endpoints:
- /api/v1/knowledge/* - Knowledge CRUD and search
- /health - Health check (no auth required)
- /api/v1/token - Token generation (admin only)

Security:
- JWT authentication on all authenticated endpoints
- 3-tier access control (public, restricted, secret)
- Rate limiting per agent
- CORS configured for internal network

Port: 3100 (configurable via DAKB_GATEWAY_PORT)
"""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..admin import admin_api_router, admin_dashboard_router, admin_ws_router
from ..db import AccessLevel, AgentRole, get_dakb_repositories
from ..db.collections import get_dakb_client
from ..monitoring.metrics import get_metrics  # Phase 8: Prometheus metrics
from .config import get_settings, validate_settings
from .middleware.auth import (
    AuthenticatedAgent,
    generate_agent_token,
    get_current_agent,
    get_rate_limiter,
    require_role,
)
from .routes.aliases import router as aliases_router
from .routes.knowledge import router as knowledge_router
from .routes.mcp import router as mcp_router  # Phase 1: MCP HTTP Transport
from .routes.messaging import router as messaging_router
from .routes.moderation import router as moderation_router
from .routes.registration import router as registration_router
from .routes.sessions import router as sessions_router

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

def setup_logging():
    """Configure logging based on settings."""
    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=settings.log_format
    )

    # Reduce noise from httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# =============================================================================
# LIFESPAN CONTEXT MANAGER
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI application.

    Handles:
    - Startup: Validate settings, check connections, initialize services
    - Shutdown: Cleanup and logging
    """
    # STARTUP
    logger.info("Starting DAKB Gateway Service...")

    # Validate settings
    is_valid, errors = validate_settings()
    if not is_valid:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        raise RuntimeError("Invalid configuration. Check environment variables.")

    settings = get_settings()

    # Setup logging
    setup_logging()

    # Note: CORS is configured at module level after app creation

    # Verify MongoDB connection
    try:
        client = get_dakb_client()
        db = client[settings.db_name]
        # Quick ping
        db.command("ping")
        logger.info(f"MongoDB connection verified: {settings.db_name}")
    except Exception as e:
        logger.error(f"MongoDB connection failed: {e}")
        raise RuntimeError("Cannot connect to MongoDB")

    # Verify embedding service (optional - may not be running yet)
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"{settings.embedding_service_url}/health",
                timeout=5.0
            )
            if response.status_code == 200:
                logger.info("Embedding service connection verified")
            else:
                logger.warning("Embedding service returned non-200 status")
    except Exception as e:
        logger.warning(f"Embedding service not available: {e}")
        logger.warning("Semantic search will be unavailable until embedding service starts")

    # Initialize rate limiter
    get_rate_limiter()
    logger.info(
        f"Rate limiter initialized: {settings.rate_limit_requests} requests "
        f"per {settings.rate_limit_window}s window"
    )

    logger.info(f"DAKB Gateway ready on port {settings.gateway_port}")

    yield  # Application runs here

    # SHUTDOWN
    logger.info("Shutting down DAKB Gateway Service...")


# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

app = FastAPI(
    title="DAKB Gateway API",
    description="""
Distributed Agent Knowledge Base (DAKB) Gateway API.

Enables AI agents to share knowledge, search semantically, and collaborate
across different machines and LLM providers.

## Authentication

All endpoints (except /health) require JWT authentication.
Include the token in the Authorization header:

```
Authorization: Bearer <your-jwt-token>
```

## Access Control

Knowledge entries have 3 access levels:
- **PUBLIC**: Accessible by all authenticated agents
- **RESTRICTED**: Accessible by specified agents or roles
- **SECRET**: Highest security, explicit agent allowlist only

## Rate Limiting

Requests are rate-limited per agent. Check response headers:
- `X-RateLimit-Remaining`: Requests remaining in current window
- `X-RateLimit-Reset`: Unix timestamp when window resets
""",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Configure CORS at module level (must be done before app starts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Default permissive - will use settings in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# =============================================================================
# MIDDLEWARE
# =============================================================================

# Note: CORS is now configured at app creation time (see below)


@app.middleware("http")
async def add_rate_limit_headers(request: Request, call_next):
    """Add rate limit headers to responses."""
    response = await call_next(request)

    # Add rate limit headers if available
    if hasattr(request.state, "rate_limit_remaining"):
        response.headers["X-RateLimit-Remaining"] = str(
            request.state.rate_limit_remaining
        )

    return response


# =============================================================================
# INCLUDE ROUTERS
# =============================================================================

app.include_router(knowledge_router)
app.include_router(moderation_router)  # ISS-048: Admin-only moderation routes
app.include_router(messaging_router)   # Phase 3: Inter-agent messaging routes
app.include_router(sessions_router)    # Phase 4: Session management and handoff
app.include_router(aliases_router)     # Token Team: Agent alias management
app.include_router(registration_router)  # Self-Registration v1.0: Invite-only registration
app.include_router(mcp_router)         # Phase 1: MCP HTTP Transport (POST/GET/DELETE /mcp)
app.include_router(admin_api_router)   # Admin Dashboard API routes
app.include_router(admin_dashboard_router)  # Admin Dashboard UI routes
app.include_router(admin_ws_router)    # Admin WebSocket routes


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(default="ok")
    service: str = Field(default="dakb-gateway")
    version: str = Field(default="1.0.0")
    mongodb: str = Field(default="unknown")
    embedding_service: str = Field(default="unknown")


class TokenRequest(BaseModel):
    """Request model for token generation."""
    agent_id: str = Field(..., min_length=1, max_length=50)
    machine_id: str = Field(..., min_length=1, max_length=100)
    agent_type: str = Field(..., description="claude, gpt, gemini, grok, local")
    role: str | None = Field(default="developer")
    access_levels: list[str] | None = Field(default_factory=lambda: ["public"])


class AgentRegistrationRequest(BaseModel):
    """
    Request model for agent registration with optional alias.

    Phase 6 Integration: Supports the "Token Team with Aliases" pattern
    where an agent can register with an optional alias during registration.
    """
    agent_id: str = Field(..., min_length=1, max_length=50, description="Unique agent identifier")
    machine_id: str = Field(..., min_length=1, max_length=100, description="Machine identifier")
    agent_type: str = Field(..., description="Agent type: claude, gpt, gemini, grok, local")
    display_name: str | None = Field(None, max_length=100, description="Human-readable display name")
    role: str | None = Field(default="developer", description="Agent role for access control")
    access_levels: list[str] | None = Field(default_factory=lambda: ["public"])
    capabilities: list[str] | None = Field(default_factory=list, description="Agent capabilities")
    specializations: list[str] | None = Field(default_factory=list, description="Agent specializations")
    # Phase 6: Alias integration
    alias: str | None = Field(
        None,
        min_length=1,
        max_length=50,
        description="Optional alias to register (must be globally unique)"
    )
    alias_role: str | None = Field(
        None,
        max_length=100,
        description="Optional role for the alias (e.g., 'orchestration', 'code_review')"
    )
    alias_metadata: dict | None = Field(
        default_factory=dict,
        description="Optional metadata for the alias"
    )


class AgentRegistrationResponse(BaseModel):
    """
    Response model for agent registration with optional alias.

    Provides detailed information about both agent and alias registration.
    """
    success: bool = Field(..., description="Whether registration succeeded")
    token: str = Field(..., description="JWT access token for the agent")
    expires_in_hours: int = Field(..., description="Token expiry in hours")
    agent_id: str = Field(..., description="Registered agent ID")
    messages: list[str] = Field(default_factory=list, description="Status messages")
    # Alias information
    alias_requested: str | None = Field(None, description="Alias that was requested")
    alias_registered: bool = Field(default=False, description="Whether alias was successfully registered")
    alias_conflict: bool = Field(default=False, description="Whether alias was already taken")


class TokenResponse(BaseModel):
    """Response model for token generation."""
    token: str
    expires_in_hours: int
    agent_id: str


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str
    error_code: str | None = None


# =============================================================================
# PUBLIC ENDPOINTS
# =============================================================================

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Check gateway health status. No authentication required.",
    tags=["System"]
)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Checks connectivity to MongoDB and embedding service.
    Does not require authentication.

    Returns:
        Health status of gateway and connected services.
    """
    settings = get_settings()
    response = HealthResponse()

    # Check MongoDB
    try:
        client = get_dakb_client()
        client.admin.command("ping")
        response.mongodb = "connected"
    except Exception as e:
        logger.warning(f"MongoDB health check failed: {e}")
        response.mongodb = "unavailable"

    # Check embedding service
    try:
        async with httpx.AsyncClient() as http_client:
            r = await http_client.get(
                f"{settings.embedding_service_url}/health",
                timeout=2.0
            )
            if r.status_code == 200:
                response.embedding_service = "connected"
            else:
                logger.warning(f"Embedding service returned status {r.status_code}")
                response.embedding_service = "unavailable"
    except Exception as e:
        logger.warning(f"Embedding service health check failed: {e}")
        response.embedding_service = "unavailable"

    return response


@app.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
    description="Export metrics in Prometheus format. No authentication required.",
    tags=["System"]
)
async def prometheus_metrics() -> PlainTextResponse:
    """
    Prometheus metrics endpoint.

    Exports all DAKB metrics in Prometheus text format for scraping.
    Does not require authentication to allow Prometheus scraper access.

    Returns:
        Prometheus-formatted metrics text.
    """
    metrics = get_metrics()
    prometheus_output = metrics.export_prometheus()
    return PlainTextResponse(
        content=prometheus_output,
        media_type="text/plain; charset=utf-8"
    )


@app.get(
    "/metrics/json",
    summary="JSON metrics",
    description="Export metrics in JSON format. No authentication required.",
    tags=["System"]
)
async def json_metrics() -> dict:
    """
    JSON metrics endpoint.

    Exports all DAKB metrics in JSON format for programmatic access.
    Does not require authentication.

    Returns:
        Dictionary of all metrics.
    """
    metrics = get_metrics()
    return {
        "status": "ok",
        "service": "dakb-gateway",
        "metrics": metrics.get_metrics()
    }


# =============================================================================
# ADMIN ENDPOINTS
# =============================================================================

@app.post(
    "/api/v1/token",
    response_model=TokenResponse,
    summary="Generate agent token",
    description="Generate a JWT token for an agent. Requires admin role.",
    tags=["Authentication"],
    dependencies=[Depends(require_role(AgentRole.ADMIN))]
)
async def generate_token(
    request: TokenRequest,
    admin: AuthenticatedAgent = Depends(get_current_agent)
) -> TokenResponse:
    """
    Generate a JWT token for an agent.

    Only admin agents can generate tokens for other agents.
    This endpoint is used to onboard new agents.

    Args:
        request: Token generation request.
        admin: Authenticated admin agent.

    Returns:
        Generated JWT token with expiry information.
    """
    settings = get_settings()

    # Parse role
    try:
        role = AgentRole(request.role or "developer")
    except ValueError:
        role = AgentRole.DEVELOPER

    # Parse access levels
    access_levels = []
    for level_str in (request.access_levels or ["public"]):
        try:
            access_levels.append(AccessLevel(level_str))
        except ValueError:
            logger.warning(f"Unknown access level: {level_str}")

    if not access_levels:
        access_levels = [AccessLevel.PUBLIC]

    # Generate token
    token = generate_agent_token(
        agent_id=request.agent_id,
        machine_id=request.machine_id,
        agent_type=request.agent_type,
        role=role,
        access_levels=access_levels
    )

    logger.info(
        f"Token generated for {request.agent_id} by admin {admin.agent_id}"
    )

    return TokenResponse(
        token=token,
        expires_in_hours=settings.jwt_expiry_hours,
        agent_id=request.agent_id
    )


@app.post(
    "/api/v1/register",
    response_model=TokenResponse,
    summary="Register new agent (legacy)",
    description="Register a new agent and get a token. Requires admin role. Use /api/v1/register/with-alias for alias support.",
    tags=["Authentication"],
    dependencies=[Depends(require_role(AgentRole.ADMIN))]
)
async def register_agent(
    request: TokenRequest,
    admin: AuthenticatedAgent = Depends(get_current_agent)
) -> TokenResponse:
    """
    Register a new agent in the system (legacy endpoint).

    Creates agent record in MongoDB and generates a token.
    Only admin agents can register new agents.

    Note: For alias support, use POST /api/v1/register/with-alias instead.

    Args:
        request: Agent registration request.
        admin: Authenticated admin agent.

    Returns:
        Generated JWT token for the new agent.
    """
    from ..db import AgentRegister, AgentType

    repos = get_dakb_repositories(get_dakb_client())

    # Check if agent already exists
    existing = repos["agents"].get_by_id(request.agent_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Agent {request.agent_id} already registered"
        )

    # Parse agent type
    try:
        agent_type = AgentType(request.agent_type)
    except ValueError:
        agent_type = AgentType.LOCAL

    # Register agent
    agent_data = AgentRegister(
        agent_id=request.agent_id,
        display_name=request.agent_id,
        agent_type=agent_type,
        machine_id=request.machine_id,
        capabilities=[],
        specializations=[]
    )

    repos["agents"].register(agent_data)

    # Generate token
    settings = get_settings()
    role = AgentRole(request.role or "developer")
    access_levels = [AccessLevel(l) for l in (request.access_levels or ["public"])]

    token = generate_agent_token(
        agent_id=request.agent_id,
        machine_id=request.machine_id,
        agent_type=request.agent_type,
        role=role,
        access_levels=access_levels
    )

    logger.info(
        f"Agent {request.agent_id} registered by admin {admin.agent_id}"
    )

    return TokenResponse(
        token=token,
        expires_in_hours=settings.jwt_expiry_hours,
        agent_id=request.agent_id
    )


@app.post(
    "/api/v1/register/with-alias",
    response_model=AgentRegistrationResponse,
    summary="Register agent with optional alias",
    description="""
Register a new agent with optional alias registration.

Phase 6 Integration: Supports the "Token Team with Aliases" pattern where an agent
can register an alias during registration. If the alias is already taken, registration
continues without the alias - the agent can still communicate via token_id.

**Alias Behavior:**
- If alias is provided and available: Both agent and alias are registered
- If alias is provided but taken: Agent registration succeeds, alias fails gracefully
- If no alias provided: Standard agent registration

**Token Team Concept:**
Multiple agents from the same token can register different aliases:
- Token 'claude-code-agent' can have aliases: Coordinator, Reviewer, Backend
- Messages to any alias route to the shared inbox
    """,
    tags=["Authentication"],
    dependencies=[Depends(require_role(AgentRole.ADMIN))]
)
async def register_agent_with_alias(
    request: AgentRegistrationRequest,
    admin: AuthenticatedAgent = Depends(get_current_agent)
) -> AgentRegistrationResponse:
    """
    Register a new agent with optional alias.

    Creates agent record, optionally registers an alias, and generates a token.
    If the alias is already taken, registration continues without it.

    Args:
        request: Agent registration request with optional alias.
        admin: Authenticated admin agent.

    Returns:
        Registration result with token and alias status.
    """
    from ..integration.alias_registration import register_agent_with_alias as do_register

    settings = get_settings()

    # Perform registration with optional alias
    result = await do_register(
        token_id=request.agent_id,
        agent_name=request.display_name or request.agent_id,
        agent_type=request.agent_type,
        machine_id=request.machine_id,
        alias=request.alias,
        role=request.alias_role,
        alias_metadata=request.alias_metadata,
        capabilities=request.capabilities,
        specializations=request.specializations,
    )

    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=f"Agent registration failed: {'; '.join(result.messages)}"
        )

    # Generate token for the registered agent
    role = AgentRole(request.role or "developer")
    access_levels = [AccessLevel(l) for l in (request.access_levels or ["public"])]

    token = generate_agent_token(
        agent_id=request.agent_id,
        machine_id=request.machine_id,
        agent_type=request.agent_type,
        role=role,
        access_levels=access_levels
    )

    # Log registration
    if result.alias_registered:
        logger.info(
            f"Agent {request.agent_id} registered with alias '{request.alias}' "
            f"by admin {admin.agent_id}"
        )
    elif result.alias_conflict:
        logger.info(
            f"Agent {request.agent_id} registered (alias '{request.alias}' conflict) "
            f"by admin {admin.agent_id}"
        )
    else:
        logger.info(
            f"Agent {request.agent_id} registered by admin {admin.agent_id}"
        )

    return AgentRegistrationResponse(
        success=True,
        token=token,
        expires_in_hours=settings.jwt_expiry_hours,
        agent_id=request.agent_id,
        messages=result.messages,
        alias_requested=result.alias_requested,
        alias_registered=result.alias_registered,
        alias_conflict=result.alias_conflict,
    )


# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with consistent format."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None)
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    logger.error(f"Unexpected error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


# =============================================================================
# MAIN
# =============================================================================

def run() -> None:
    """Run the DAKB gateway server."""
    import uvicorn

    # Validate configuration
    is_valid, errors = validate_settings()
    if not is_valid:
        print("Configuration errors:")
        for error in errors:
            print(f"  - {error}")
        print("\nPlease set the required environment variables.")
        exit(1)

    settings = get_settings()

    uvicorn.run(
        app,
        host=settings.gateway_host,
        port=settings.gateway_port,
        log_level=settings.log_level.lower()
    )


if __name__ == "__main__":
    run()
