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

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from ..admin import (
    admin_api_router,
    admin_dashboard_router,
    admin_whiteboard_router,
    admin_ws_router,
)
from ..db import AccessLevel, AgentRole, get_dakb_repositories
from ..db.collections import get_dakb_client
from ..monitoring.metrics import get_metrics  # Phase 8: Prometheus metrics
from .agentic import register_dakb_agentic_handlers  # Agentic API exception handlers
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
from .routes.threads import router as threads_router  # Knowledge Threads
from .routes.whiteboard import router as whiteboard_router  # HIVE Whiteboard

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================


def setup_logging():
    """Configure logging based on settings."""
    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO), format=settings.log_format
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

    Graceful degradation: every optional subsystem (MongoDB-backed features,
    Redis real-time, chat bridge, session bridge) is wrapped in try/except.
    The app starts even when MongoDB and Redis are completely unavailable —
    affected features simply stay disabled with a warning log.
    """
    # STARTUP
    logger.info("Starting DAKB Gateway Service...")

    # Setup logging
    setup_logging()

    # Validate settings (warn, never crash — allows boot without full env)
    is_valid, errors = validate_settings()
    if not is_valid:
        for error in errors:
            logger.warning(f"Configuration warning: {error}")

    settings = get_settings()

    # ── MongoDB (optional — features that need it are skipped if unavailable) ──
    # Subsystem routers are already MOUNTED at module level (see _wire_subsystems);
    # here we only perform LIVE operations (ping, index creation, repo injection,
    # connect, background tasks) — each independently guarded so the gateway boots
    # with no MongoDB and no Redis running.
    db = getattr(app.state, "db", None)
    # mongo_ok gates every Mongo-dependent startup step. The motor/pymongo
    # `db` handle is non-None even when the server is unreachable, so a
    # `db is not None` guard is NOT sufficient — running index creation against
    # an unreachable server blocks on server-selection timeouts. Only proceed
    # once the ping actually succeeds.
    mongo_ok = False
    try:
        if db is not None:
            db.command("ping")
            mongo_ok = True
            logger.info(f"MongoDB connection verified: {settings.db_name}")
    except Exception as e:
        logger.warning(
            f"MongoDB not available: {e} — knowledge/whiteboard/vault/bridge "
            "features will operate in degraded mode"
        )

    # ── Indexes (idempotent; covers all v2.0.0 collections) ──
    if mongo_ok:
        try:
            from ..db.indexes import create_all_indexes

            create_all_indexes(db)
            logger.info("MongoDB indexes ensured (create_all_indexes)")
        except Exception as e:
            logger.warning(f"Index creation skipped: {e}")

    # ── HIVE Whiteboard repository injection ──
    if mongo_ok:
        try:
            from ..db.whiteboard_indexes import initialize_whiteboard_indexes
            from ..db.whiteboard_repository import WhiteboardRepository
            from .routes.whiteboard import set_repository as set_whiteboard_repo

            set_whiteboard_repo(WhiteboardRepository(db))
            initialize_whiteboard_indexes(db)
            logger.info("Whiteboard repository initialized")
        except Exception as e:
            logger.warning(f"Whiteboard not available: {e}")

    # ── Embedding service (optional — semantic search degrades without it) ──
    try:
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"{settings.embedding_service_url}/health", timeout=5.0
            )
            if response.status_code == 200:
                logger.info("Embedding service connection verified")
            else:
                logger.warning("Embedding service returned non-200 status")
    except Exception as e:
        logger.warning(f"Embedding service not available: {e}")
        logger.warning("Semantic search will be unavailable until embedding service starts")

    # ── Rate limiter ──
    get_rate_limiter()
    logger.info(
        f"Rate limiter initialized: {settings.rate_limit_requests} requests "
        f"per {settings.rate_limit_window}s window"
    )

    # ── Notification bus (in-process; no external infra required) ──
    try:
        from .notification_bus import get_notification_bus

        await get_notification_bus()
        logger.info("Notification bus started")
    except Exception as e:
        logger.warning(f"Notification bus not available: {e}")

    # ── Redis real-time stack (optional — disabled cleanly if Redis is down) ──
    # The RedisClient, managers, routers and bridge objects were all built at
    # module level by _wire_subsystems() and stored on app.state. Here we only
    # establish the live connection and start the monitors / background tasks.
    redis_client = getattr(app.state, "redis_client", None)
    try:
        if redis_client is None:
            raise RuntimeError("Redis client not constructed")

        connected = await redis_client.connect()
        if not connected:
            raise RuntimeError(f"Redis connect() returned False ({settings.redis_url})")
        logger.info(f"Redis connected: {settings.redis_url}")
        app.state.redis_connected = True

        # Task timeout monitor (needs MongoDB-backed TaskRouter).
        task_monitor = getattr(app.state, "task_monitor", None)
        if task_monitor is not None:
            asyncio.create_task(task_monitor.start())
            logger.info("Task delegation enabled (TaskRouter + TaskTimeoutMonitor)")

        # Background outbound delivery loop (cancelled on shutdown).
        outbound_consumer = getattr(app.state, "outbound_consumer", None)
        if outbound_consumer is not None:
            app.state.outbound_task = asyncio.create_task(_run_outbound_consumer(outbound_consumer))
            logger.info("Chat outbound consumer started")

        logger.info("Real-time communication enabled (WebSocket + Presence + Router + Bridge)")
    except Exception as e:
        logger.warning(
            f"Redis/real-time not available: {e} — real-time, chat, and bridge "
            "live features disabled (routes remain mounted but degrade gracefully)"
        )
        app.state.redis_connected = False

    logger.info(f"DAKB Gateway ready on port {settings.gateway_port}")

    yield  # Application runs here

    # SHUTDOWN
    logger.info("Shutting down DAKB Gateway Service...")

    outbound_task = getattr(app.state, "outbound_task", None)
    if outbound_task:
        outbound_task.cancel()
        try:
            await outbound_task
        except (asyncio.CancelledError, Exception):
            pass
        logger.info("Outbound consumer task stopped")

    task_monitor = getattr(app.state, "task_monitor", None)
    if task_monitor:
        try:
            await task_monitor.stop()
            logger.info("Task monitor stopped")
        except Exception as e:
            logger.warning(f"Task monitor stop failed: {e}")

    try:
        from .notification_bus import shutdown_notification_bus

        await shutdown_notification_bus()
        logger.info("Notification bus stopped")
    except Exception:
        pass

    motor_client = getattr(app.state, "motor_client", None)
    if motor_client:
        motor_client.close()
        logger.info("Motor client closed")

    bridge_raw_redis = getattr(app.state, "bridge_raw_redis", None)
    if bridge_raw_redis:
        try:
            await bridge_raw_redis.aclose()
        except Exception:
            pass

    if getattr(app.state, "redis_client", None):
        try:
            await app.state.redis_client.disconnect()
            logger.info("Redis disconnected")
        except Exception:
            pass


async def _run_outbound_consumer(consumer, poll_interval: float = 1.0) -> None:
    """Background loop draining the chat outbound stream.

    The open ``OutboundConsumer`` exposes ``run_once(count)`` (a single batch
    read) rather than a built-in loop, so we drive it here as a cancellable
    background task. Errors are logged and swallowed so a transient Redis hiccup
    never kills the loop.
    """
    while True:
        try:
            await consumer.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Outbound consumer batch failed: {e}")
        await asyncio.sleep(poll_interval)


async def handle_bridge_ws(websocket, session_id: str, bridge_conn) -> None:
    """WebSocket handler for bridge clients (``/ws/bridge/{session_id}``).

    Accepts the connection, registers it with the BridgeConnectionManager,
    delivers any queued backlog, then pumps inbound frames as heartbeats until
    the client disconnects. Cleanup always runs.
    """
    from starlette.websockets import WebSocketDisconnect

    await websocket.accept()
    try:
        await bridge_conn.on_connect(session_id, websocket)
        while True:
            await websocket.receive_text()
            await bridge_conn.update_heartbeat(session_id)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"Bridge WS error for {session_id}: {e}")
    finally:
        await bridge_conn.cleanup(session_id)


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
    openapi_url="/openapi.json",
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
# SUBSYSTEM WIRING (v2.0.0)
# =============================================================================


def _wire_subsystems(app: FastAPI) -> None:
    """Construct and mount the v2.0.0 real-time subsystems on ``app``.

    Runs once at module import. Everything here is build-safe: dependency
    objects are constructed lazily (pymongo/motor handles do not connect until a
    query is issued, the local vault backend only resolves a path, the chat
    adapter registry reads env only). No network call happens at import time.

    Each subsystem is independently guarded — a failure to wire one never
    prevents the app object from being built. Live connection / monitor / index
    work is deferred to the lifespan, which degrades gracefully if Redis or
    MongoDB are down. Handles are stashed on ``app.state`` for the lifespan and
    for request-time dependency injection.
    """
    settings = get_settings()

    # Initialise tracked lifecycle handles.
    app.state.db = None
    app.state.redis_client = None
    app.state.task_monitor = None
    app.state.task_router = None
    app.state.outbound_consumer = None
    app.state.outbound_task = None
    app.state.motor_client = None
    app.state.settings = settings

    # ── MongoDB handle (lazy; does not connect here) ──
    db = None
    try:
        db = get_dakb_client()[settings.db_name]
        app.state.db = db
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"MongoDB client unavailable at wiring time: {e}")

    # ── HIVE File Vault router ──
    try:
        from ..db.collections import KnowledgeRepository
        from ..db.repositories.vault_repository import VaultFileRepository
        from ..vault import create_vault_backend
        from .routes.vault import create_vault_router

        if db is not None:
            knowledge_col = db["dakb_knowledge"]
            vault_repo = VaultFileRepository(db["dakb_vault_files"], knowledge_col)
            knowledge_repo = KnowledgeRepository(knowledge_col)
        else:
            vault_repo = None
            knowledge_repo = None
        vault_backend = create_vault_backend(settings.vault.to_backend_config())
        app.include_router(
            create_vault_router(
                vault_repo,
                vault_backend,
                settings.vault,
                knowledge_repo=knowledge_repo,
            )
        )
        logger.info(f"File Vault router mounted (backend={settings.vault.backend})")
    except Exception as e:
        logger.warning(f"File Vault router not mounted: {e}")

    # ── Redis client + real-time managers (constructed, not yet connected) ──
    try:
        from .agent_websocket import AgentConnectionManager
        from .agent_websocket import router as agent_ws_router
        from .message_router import MessageRouter
        from .presence import PresenceManager
        from .redis_client import RedisClient
        from .ws_rate_limiter import WSRateLimiter

        redis_client = RedisClient(settings.redis_url)
        conn_manager = AgentConnectionManager()
        presence_manager = PresenceManager(redis_client=redis_client)
        ws_rate_limiter = WSRateLimiter()

        # Task delegation (MongoDB-backed). Constructed if a db handle exists.
        task_router = None
        if db is not None:
            try:
                from .task_monitor import TaskTimeoutMonitor
                from .task_router import TaskRouter

                tasks_collection = db["dakb_tasks"]
                task_router = TaskRouter(
                    redis_client=redis_client,
                    presence_manager=presence_manager,
                    conn_manager=conn_manager,
                    mongo_collection=tasks_collection,
                )
                app.state.task_monitor = TaskTimeoutMonitor(
                    task_router=task_router,
                    redis_client=redis_client,
                    conn_manager=conn_manager,
                    mongo_collection=tasks_collection,
                    check_interval=10.0,
                )
            except Exception as e:
                logger.warning(f"Task delegation not wired: {e}")

        # ── Chat Bridge (adapters + sessions + outbound consumer) ──
        outbound_consumer = None
        try:
            from ..chat_bridge.outbound_consumer import OutboundConsumer
            from ..chat_bridge.registry import AdapterRegistry
            from ..chat_bridge.router import create_chat_router
            from ..chat_bridge.session_manager import ChatSessionManager

            adapter_registry = AdapterRegistry()
            loaded_adapters = adapter_registry.auto_load()

            session_manager = (
                ChatSessionManager(db["dakb_chat_sessions"]) if db is not None else None
            )
            if session_manager is not None:
                outbound_consumer = OutboundConsumer(
                    redis_client=redis_client,
                    adapter_registry=adapter_registry,
                    session_manager=session_manager,
                )
            app.state.adapter_registry = adapter_registry
            app.state.session_manager = session_manager
            app.state.outbound_consumer = outbound_consumer

            # Webhook router (/webhook/{platform}) — mount regardless of tokens.
            app.include_router(
                create_chat_router(
                    adapter_registry=adapter_registry,
                    redis_client=redis_client,
                    session_manager=session_manager,
                )
            )
            logger.info(
                f"Chat Bridge router mounted ({len(loaded_adapters)} adapter(s) "
                f"loaded{': ' + ', '.join(loaded_adapters) if loaded_adapters else ''})"
            )
        except Exception as e:
            logger.warning(f"Chat Bridge router not mounted: {e}")

        # Single MessageRouter wired with every available collaborator.
        message_router = MessageRouter(
            redis_client=redis_client,
            conn_manager=conn_manager,
            presence_manager=presence_manager,
            task_router=task_router,
            session_manager=app.state.session_manager,
            outbound_consumer=outbound_consumer,
            alert_config_collection=(db["dakb_alert_config"] if db is not None else None),
        )

        app.state.redis_client = redis_client
        app.state.conn_manager = conn_manager
        app.state.presence_manager = presence_manager
        app.state.ws_rate_limiter = ws_rate_limiter
        app.state.message_router = message_router
        app.state.task_router = task_router

        # Agent WebSocket router.
        app.include_router(agent_ws_router)
        logger.info("Agent WebSocket router mounted")

        # ── Session Bridge (REST router + bridge WS route) ──
        try:
            import motor.motor_asyncio as motor_aio

            # The bridge components want the raw aioredis client. RedisClient.redis
            # raises until connect(), so build a lazy raw client directly here —
            # from_url() does NOT connect until the first command is issued, so this
            # is build-safe and works as soon as Redis comes up at request time.
            import redis.asyncio as _aioredis

            from ..bridge.queue import BridgeQueueManager
            from ..bridge.routes import create_bridge_router
            from ..bridge.ws_handler import BridgeConnectionManager

            raw_redis = _aioredis.from_url(settings.redis_url, decode_responses=True)

            motor_client = motor_aio.AsyncIOMotorClient(settings.bridge_mongo_uri)
            motor_db = motor_client[settings.db_name]
            bridge_links_collection = motor_db["dakb_bridge_links"]
            bridge_queue_collection = motor_db["dakb_bridge_queue"]

            bridge_queue = BridgeQueueManager(
                redis=raw_redis,
                collection=bridge_queue_collection,
            )
            bridge_conn = BridgeConnectionManager(
                redis=raw_redis,
                queue=bridge_queue,
            )

            async def _bridge_ws(websocket: WebSocket, session_id: str):
                await handle_bridge_ws(websocket, session_id, bridge_conn)

            app.add_api_websocket_route("/ws/bridge/{session_id}", _bridge_ws)
            app.include_router(
                create_bridge_router(
                    queue=bridge_queue,
                    links_collection=bridge_links_collection,
                    redis=raw_redis,
                    outbound_consumer=outbound_consumer,
                ),
                prefix="/api/v1",
            )
            app.state.bridge_raw_redis = raw_redis
            app.state.bridge_queue = bridge_queue
            app.state.bridge_conn = bridge_conn
            app.state.bridge_links_collection = bridge_links_collection
            app.state.motor_client = motor_client
            logger.info("Session Bridge router + /ws/bridge mounted")
        except Exception as e:
            logger.warning(f"Session Bridge not mounted: {e}")

    except Exception as e:
        logger.warning(f"Real-time subsystem wiring failed: {e}")


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
        response.headers["X-RateLimit-Remaining"] = str(request.state.rate_limit_remaining)

    return response


# =============================================================================
# INCLUDE ROUTERS
# =============================================================================

# Register agentic API exception handlers BEFORE including routers so that
# errors raised from any route (including Depends()) get agentic remediation.
register_dakb_agentic_handlers(app)

app.include_router(knowledge_router)
app.include_router(moderation_router)  # ISS-048: Admin-only moderation routes
app.include_router(messaging_router)  # Phase 3: Inter-agent messaging routes
app.include_router(sessions_router)  # Phase 4: Session management and handoff
app.include_router(aliases_router)  # Token Team: Agent alias management
app.include_router(registration_router)  # Self-Registration v1.0: Invite-only registration
app.include_router(threads_router)  # Knowledge Threads (comments, suggestions, endorsements)
app.include_router(whiteboard_router)  # HIVE Whiteboard (repository injected during lifespan)
app.include_router(mcp_router)  # Phase 1: MCP HTTP Transport (POST/GET/DELETE /mcp)
app.include_router(admin_api_router)  # Admin Dashboard API routes
app.include_router(admin_dashboard_router)  # Admin Dashboard UI routes
app.include_router(admin_whiteboard_router)  # Admin Whiteboard panel (GET /admin/whiteboard)
app.include_router(admin_ws_router)  # Admin WebSocket routes

# v2.0.0 real-time subsystems (vault, agent-WS, chat webhooks, session bridge).
# These routers are mounted here at module level so their route paths exist on the
# app (OpenAPI, routing) even before — and regardless of whether — Redis/MongoDB
# come up. The objects are constructed lazily (no live connections at build time);
# the lifespan performs the actual connect / index / monitor / consumer work and
# degrades gracefully when infra is unavailable.
_wire_subsystems(app)


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
    redis: str = Field(default="unknown")


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
    display_name: str | None = Field(
        None, max_length=100, description="Human-readable display name"
    )
    role: str | None = Field(default="developer", description="Agent role for access control")
    access_levels: list[str] | None = Field(default_factory=lambda: ["public"])
    capabilities: list[str] | None = Field(default_factory=list, description="Agent capabilities")
    specializations: list[str] | None = Field(
        default_factory=list, description="Agent specializations"
    )
    # Phase 6: Alias integration
    alias: str | None = Field(
        None,
        min_length=1,
        max_length=50,
        description="Optional alias to register (must be globally unique)",
    )
    alias_role: str | None = Field(
        None,
        max_length=100,
        description="Optional role for the alias (e.g., 'orchestration', 'code_review')",
    )
    alias_metadata: dict | None = Field(
        default_factory=dict, description="Optional metadata for the alias"
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
    alias_registered: bool = Field(
        default=False, description="Whether alias was successfully registered"
    )
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
    tags=["System"],
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
            r = await http_client.get(f"{settings.embedding_service_url}/health", timeout=2.0)
            if r.status_code == 200:
                response.embedding_service = "connected"
            else:
                logger.warning(f"Embedding service returned status {r.status_code}")
                response.embedding_service = "unavailable"
    except Exception as e:
        logger.warning(f"Embedding service health check failed: {e}")
        response.embedding_service = "unavailable"

    # Check Redis (real-time communication)
    redis_client = getattr(app.state, "redis_client", None)
    if redis_client is not None:
        try:
            response.redis = "connected" if await redis_client.health_check() else "unavailable"
        except Exception as e:
            logger.warning(f"Redis health check failed: {e}")
            response.redis = "error"
    else:
        response.redis = "not_configured"

    return response


@app.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
    description="Export metrics in Prometheus format. No authentication required.",
    tags=["System"],
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
    return PlainTextResponse(content=prometheus_output, media_type="text/plain; charset=utf-8")


@app.get(
    "/metrics/json",
    summary="JSON metrics",
    description="Export metrics in JSON format. No authentication required.",
    tags=["System"],
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
    return {"status": "ok", "service": "dakb-gateway", "metrics": metrics.get_metrics()}


# =============================================================================
# ADMIN ENDPOINTS
# =============================================================================


@app.post(
    "/api/v1/token",
    response_model=TokenResponse,
    summary="Generate agent token",
    description="Generate a JWT token for an agent. Requires admin role.",
    tags=["Authentication"],
    dependencies=[Depends(require_role(AgentRole.ADMIN))],
)
async def generate_token(
    request: TokenRequest, admin: AuthenticatedAgent = Depends(get_current_agent)
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
    for level_str in request.access_levels or ["public"]:
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
        access_levels=access_levels,
    )

    logger.info(f"Token generated for {request.agent_id} by admin {admin.agent_id}")

    return TokenResponse(
        token=token, expires_in_hours=settings.jwt_expiry_hours, agent_id=request.agent_id
    )


@app.post(
    "/api/v1/register",
    response_model=TokenResponse,
    summary="Register new agent (legacy)",
    description="Register a new agent and get a token. Requires admin role. Use /api/v1/register/with-alias for alias support.",
    tags=["Authentication"],
    dependencies=[Depends(require_role(AgentRole.ADMIN))],
)
async def register_agent(
    request: TokenRequest, admin: AuthenticatedAgent = Depends(get_current_agent)
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
        raise HTTPException(status_code=409, detail=f"Agent {request.agent_id} already registered")

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
        specializations=[],
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
        access_levels=access_levels,
    )

    logger.info(f"Agent {request.agent_id} registered by admin {admin.agent_id}")

    return TokenResponse(
        token=token, expires_in_hours=settings.jwt_expiry_hours, agent_id=request.agent_id
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
- A single team token can have aliases such as: Lead, Reviewer, Backend
- Messages to any alias route to the shared inbox
    """,
    tags=["Authentication"],
    dependencies=[Depends(require_role(AgentRole.ADMIN))],
)
async def register_agent_with_alias(
    request: AgentRegistrationRequest, admin: AuthenticatedAgent = Depends(get_current_agent)
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
            status_code=500, detail=f"Agent registration failed: {'; '.join(result.messages)}"
        )

    # Generate token for the registered agent
    role = AgentRole(request.role or "developer")
    access_levels = [AccessLevel(l) for l in (request.access_levels or ["public"])]

    token = generate_agent_token(
        agent_id=request.agent_id,
        machine_id=request.machine_id,
        agent_type=request.agent_type,
        role=role,
        access_levels=access_levels,
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
        logger.info(f"Agent {request.agent_id} registered by admin {admin.agent_id}")

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
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    logger.error(f"Unexpected error: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


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
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
