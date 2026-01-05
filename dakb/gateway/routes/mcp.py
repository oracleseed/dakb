"""
DAKB MCP HTTP Transport - Streamable HTTP Implementation

MCP 2025-03-26 Streamable HTTP transport for DAKB Gateway.
Implements POST/GET/DELETE /mcp endpoints for MCP protocol over HTTP.

Version: 1.0
Created: 2025-12-17
Author: Backend Agent (Claude Opus 4.5)
Session: sess_20251217_174528_23429cfe
Phase: 1 - Core MCP HTTP Transport

Features:
- POST /mcp: JSON-RPC request handling
- Session management via Mcp-Session-Id header
- Origin and Accept header validation
- JSON-RPC compliant error handling
- tools/list with profile support (standard/full)
- tools/call with direct repository handlers

Endpoints:
- POST /mcp: Process JSON-RPC requests (initialize, tools/list, tools/call)
- GET /mcp: SSE stream for server-initiated messages (Phase 2)
- DELETE /mcp: Terminate session (Phase 2)

JSON-RPC Methods:
- initialize: Create MCP session, return protocol info
- tools/list: Return available tools (profile-based)
- tools/call: Execute a tool and return result

Configuration (environment variables):
- DAKB_MCP_ALLOWED_ORIGINS: Comma-separated allowed origins (default: localhost)
- DAKB_MCP_DEPLOYMENT_MODE: Deployment mode (local/lan/cloud)
- DAKB_PROFILE: Tool profile (standard/full)
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Request, Response, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..middleware.auth import get_current_agent, AuthenticatedAgent
from ..mcp_session import (
    get_session_store_async,
    MCPSession,
    SessionNotFoundError,
    SessionOwnershipError,
    generate_session_id,
)

logger = logging.getLogger(__name__)

# =============================================================================
# ROUTER CONFIGURATION
# =============================================================================

router = APIRouter(
    prefix="/mcp",
    tags=["MCP Transport"],
)


# =============================================================================
# CONFIGURATION
# =============================================================================

def _get_env_list(key: str, default: list[str]) -> list[str]:
    """Get list environment variable (comma-separated) with default."""
    value = os.getenv(key)
    if value is None:
        return default
    return [item.strip() for item in value.split(',') if item.strip()]


class MCPHTTPConfig:
    """MCP HTTP transport configuration."""

    # Allowed origins by deployment mode
    ALLOWED_ORIGINS_BY_MODE = {
        "local": ["http://localhost", "http://127.0.0.1", "null"],
        "lan": ["http://192.168.", "http://10."],  # Prefixes, no null
        "cloud": [],  # Must be explicitly configured
    }

    # Protocol version
    PROTOCOL_VERSION = "2025-03-26"

    # Server info
    SERVER_INFO = {
        "name": "dakb-server",
        "version": "1.0.0",
    }

    @classmethod
    def get_deployment_mode(cls) -> str:
        """Get current deployment mode."""
        return os.getenv("DAKB_MCP_DEPLOYMENT_MODE", "local")

    @classmethod
    def get_allowed_origins(cls) -> list[str]:
        """Get allowed origins based on deployment mode."""
        mode = cls.get_deployment_mode()
        custom_origins = _get_env_list("DAKB_MCP_ALLOWED_ORIGINS", [])

        if custom_origins:
            return custom_origins

        return cls.ALLOWED_ORIGINS_BY_MODE.get(mode, cls.ALLOWED_ORIGINS_BY_MODE["local"])

    @classmethod
    def get_profile(cls) -> str:
        """Get tool profile setting."""
        return os.getenv("DAKB_PROFILE", "standard")


# =============================================================================
# JSON-RPC MODELS
# =============================================================================

class JSONRPCRequest(BaseModel):
    """JSON-RPC 2.0 request."""
    jsonrpc: str = Field(default="2.0", description="JSON-RPC version")
    id: Optional[int | str] = Field(default=None, description="Request ID")
    method: str = Field(..., description="Method name")
    params: Optional[dict[str, Any]] = Field(default=None, description="Method parameters")


class JSONRPCError(BaseModel):
    """JSON-RPC 2.0 error object."""
    code: int = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    data: Optional[dict[str, Any]] = Field(default=None, description="Additional data")


class JSONRPCResponse(BaseModel):
    """JSON-RPC 2.0 response."""
    jsonrpc: str = Field(default="2.0")
    id: Optional[int | str] = Field(default=None)
    result: Optional[dict[str, Any]] = Field(default=None)
    error: Optional[JSONRPCError] = Field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, excluding None values appropriately."""
        resp: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            resp["error"] = self.error.model_dump()
        else:
            resp["result"] = self.result
        return resp


# JSON-RPC Standard Error Codes
class JSONRPCErrorCodes:
    """Standard JSON-RPC 2.0 error codes."""
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Server errors (DAKB-specific: -32000 to -32099)
    SERVER_ERROR = -32000
    SESSION_ERROR = -32001
    AUTH_ERROR = -32002
    TOOL_ERROR = -32003


# =============================================================================
# SECURITY VALIDATION
# =============================================================================

def validate_origin(request: Request) -> None:
    """
    Validate Origin header for DNS rebinding protection.

    Rules:
    - If Origin present: must match allowed origins
    - If Origin absent: allow (assumes non-browser client)
    - Origin: null is only allowed in local mode

    Raises:
        HTTPException: If origin validation fails
    """
    from urllib.parse import urlparse

    origin = request.headers.get("origin")

    if origin is None:
        # No origin header - non-browser client, allow
        return

    mode = MCPHTTPConfig.get_deployment_mode()
    allowed = MCPHTTPConfig.get_allowed_origins()

    # Check null origin
    if origin == "null":
        if mode != "local":
            logger.warning(f"Rejected null origin in {mode} mode")
            raise HTTPException(
                status_code=403,
                detail="Origin header 'null' not allowed in this deployment mode"
            )
        return

    # MEDIUM-1 FIX: Use URL parsing to validate hostname properly
    # This prevents prefix attacks like "http://localhost.evil.com"
    try:
        origin_parsed = urlparse(origin)
        origin_host = origin_parsed.hostname or ""
    except Exception:
        logger.warning(f"Failed to parse origin: {origin}")
        raise HTTPException(
            status_code=403,
            detail=f"Invalid origin format: '{origin}'"
        )

    # Check against allowed origins
    for allowed_origin in allowed:
        if allowed_origin == origin:
            # Exact match
            return

        try:
            allowed_parsed = urlparse(allowed_origin)
            allowed_host = allowed_parsed.hostname or ""

            # For LAN mode prefixes like "http://192.168.", check IP prefix
            if allowed_host and origin_host:
                # Exact hostname match
                if origin_host == allowed_host:
                    return
                # IP prefix match for LAN mode (e.g., "192.168." matches "192.168.0.12")
                if mode == "lan" and origin_host.startswith(allowed_host.rstrip(".")):
                    return
        except Exception:
            continue

    logger.warning(f"Rejected origin: {origin}")
    raise HTTPException(
        status_code=403,
        detail=f"Origin '{origin}' not allowed"
    )


def validate_accept_header(request: Request) -> str:
    """
    Validate Accept header for content negotiation.

    MCP 2025-03-26 requires:
    - application/json for JSON response
    - text/event-stream for SSE streaming

    Returns:
        Response content type to use

    Raises:
        HTTPException: If Accept header is invalid
    """
    accept = request.headers.get("accept", "application/json")

    # Normalize and check
    accept_types = [t.strip().split(";")[0] for t in accept.split(",")]

    if "application/json" in accept_types or "*/*" in accept_types:
        return "application/json"

    if "text/event-stream" in accept_types:
        return "text/event-stream"

    raise HTTPException(
        status_code=406,
        detail="Accept header must include application/json or text/event-stream"
    )


# =============================================================================
# SESSION MANAGEMENT HELPERS
# =============================================================================

async def get_or_create_session(
    request: Request,
    agent: AuthenticatedAgent,
) -> tuple[MCPSession, bool]:
    """
    Get existing session or create new one.

    Args:
        request: FastAPI request
        agent: Authenticated agent

    Returns:
        Tuple of (session, is_new)
    """
    # CRITICAL-1 FIX: Use async-safe session store initialization
    session_store = await get_session_store_async()
    session_id = request.headers.get("Mcp-Session-Id")

    if session_id:
        # Validate existing session
        try:
            session = await session_store.validate(session_id, agent.agent_id)
            return session, False
        except (SessionNotFoundError, SessionOwnershipError) as e:
            logger.warning(f"Session validation failed: {e}")
            raise HTTPException(
                status_code=404 if isinstance(e, SessionNotFoundError) else 403,
                detail=str(e)
            )

    # No session header - will be created on initialize
    return None, True


# =============================================================================
# TOOL EXECUTION
# =============================================================================

async def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    agent: AuthenticatedAgent,
    session: Optional[MCPSession],
) -> dict[str, Any]:
    """
    Execute a DAKB tool and return the result.

    Args:
        tool_name: Name of the tool to execute
        arguments: Tool arguments
        agent: Authenticated agent
        session: MCP session (optional)

    Returns:
        Tool execution result

    Raises:
        ValueError: If tool not found or validation fails
    """
    # Import handlers here to avoid circular imports
    from ...mcp.handlers import (
        handle_store_knowledge,
        handle_search,
        handle_get_knowledge,
        handle_vote,
        handle_status,
        handle_get_stats,
        handle_send_message,
        handle_get_messages,
        handle_mark_read,
        handle_broadcast,
        handle_get_message_stats,
        handle_advanced,
        get_client,
    )
    from ...mcp.tools import validate_tool_args, ADVANCED_TOOL_NAMES

    # CRITICAL-2 FIX: Validate tool arguments against schema BEFORE execution
    # This prevents injection attacks and ensures type safety
    is_valid, error_msg = validate_tool_args(tool_name, arguments or {})
    if not is_valid:
        raise ValueError(f"Invalid tool arguments: {error_msg}")

    # Map tool names to handlers
    # NOTE: All handlers take a single 'args' dict parameter
    TOOL_HANDLERS = {
        # Core CRUD
        "dakb_store_knowledge": handle_store_knowledge,
        "dakb_search": handle_search,
        "dakb_get_knowledge": handle_get_knowledge,
        "dakb_vote": handle_vote,
        "dakb_status": handle_status,
        # Statistics
        "dakb_get_stats": handle_get_stats,
        # Messaging
        "dakb_send_message": handle_send_message,
        "dakb_get_messages": handle_get_messages,
        "dakb_mark_read": handle_mark_read,
        "dakb_broadcast": handle_broadcast,
        "dakb_get_message_stats": handle_get_message_stats,
        # Proxy for advanced operations
        "dakb_advanced": handle_advanced,
    }

    # Check if tool exists
    if tool_name not in TOOL_HANDLERS:
        # Check if it's an advanced tool
        if tool_name.replace("dakb_", "") in [n.replace("dakb_", "") for n in ADVANCED_TOOL_NAMES]:
            # Redirect to advanced handler
            result = await handle_advanced({
                "operation": tool_name.replace("dakb_", ""),
                "params": arguments,
            })
            return result.to_dict() if hasattr(result, 'to_dict') else result
        raise ValueError(f"Unknown tool: {tool_name}")

    handler = TOOL_HANDLERS[tool_name]

    # Execute handler - all handlers expect a single 'args' dict
    try:
        result = await handler(arguments)
        return result.to_dict() if hasattr(result, 'to_dict') else result
    except Exception as e:
        logger.error(f"Tool execution error for {tool_name}: {e}")
        raise


# =============================================================================
# JSON-RPC METHOD HANDLERS
# =============================================================================

async def handle_initialize(
    params: Optional[dict[str, Any]],
    agent: AuthenticatedAgent,
    request: Request,
) -> tuple[dict[str, Any], str]:
    """
    Handle 'initialize' JSON-RPC method.

    Creates a new MCP session and returns protocol information.

    Args:
        params: Method parameters
        agent: Authenticated agent
        request: FastAPI request

    Returns:
        Tuple of (result dict, session_id)
    """
    # CRITICAL-1 FIX: Use async-safe session store initialization
    session_store = await get_session_store_async()

    # Check if already have a session
    existing_session_id = request.headers.get("Mcp-Session-Id")
    if existing_session_id:
        # Validate and return existing session info
        try:
            session = await session_store.validate(existing_session_id, agent.agent_id)
            return {
                "protocolVersion": MCPHTTPConfig.PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                    "experimental": {
                        "dakb": {
                            "profile": MCPHTTPConfig.get_profile(),
                            "messaging": True,
                            "sessions": True,
                            "aliases": True,
                        }
                    }
                },
                "serverInfo": MCPHTTPConfig.SERVER_INFO,
            }, session.session_id
        except (SessionNotFoundError, SessionOwnershipError):
            pass  # Create new session

    # Extract metadata from params
    client_info = params.get("clientInfo", {}) if params else {}

    # Create new session
    try:
        session = await session_store.create(
            agent_id=agent.agent_id,
            machine_id=agent.machine_id,
            metadata={
                "client_info": client_info,
                "protocol_version": MCPHTTPConfig.PROTOCOL_VERSION,
            }
        )
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))

    logger.info(f"MCP session created: {session.session_id} for agent {agent.agent_id}")

    return {
        "protocolVersion": MCPHTTPConfig.PROTOCOL_VERSION,
        "capabilities": {
            "tools": {},
            "experimental": {
                "dakb": {
                    "profile": MCPHTTPConfig.get_profile(),
                    "messaging": True,
                    "sessions": True,
                    "aliases": True,
                }
            }
        },
        "serverInfo": MCPHTTPConfig.SERVER_INFO,
    }, session.session_id


async def handle_tools_list(
    params: Optional[dict[str, Any]],
    agent: AuthenticatedAgent,
) -> dict[str, Any]:
    """
    Handle 'tools/list' JSON-RPC method.

    Returns available tools based on profile setting.

    Args:
        params: Method parameters (cursor for pagination, if needed)
        agent: Authenticated agent

    Returns:
        Tools list result
    """
    from ...mcp.tools import get_tools_by_profile

    profile = MCPHTTPConfig.get_profile()
    tools = get_tools_by_profile(profile)

    # Transform to MCP format
    mcp_tools = []
    for tool in tools:
        mcp_tools.append({
            "name": tool["name"],
            "description": tool.get("description", ""),
            "inputSchema": tool.get("inputSchema", {"type": "object", "properties": {}}),
        })

    return {
        "tools": mcp_tools,
    }


async def handle_tools_call(
    params: dict[str, Any],
    agent: AuthenticatedAgent,
    session: Optional[MCPSession],
) -> dict[str, Any]:
    """
    Handle 'tools/call' JSON-RPC method.

    Executes a tool and returns the result.

    Args:
        params: Method parameters (name, arguments)
        agent: Authenticated agent
        session: MCP session

    Returns:
        Tool call result
    """
    if not params:
        raise ValueError("Missing params for tools/call")

    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    if not tool_name:
        raise ValueError("Missing tool name in params")

    try:
        result = await execute_tool(tool_name, arguments, agent, session)

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2),
                }
            ],
            "isError": not result.get("success", True) if isinstance(result, dict) else False,
        }
    except ValueError as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"error": str(e)}),
                }
            ],
            "isError": True,
        }
    except Exception as e:
        logger.error(f"Tool call error: {e}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }),
                }
            ],
            "isError": True,
        }


# =============================================================================
# JSON-RPC REQUEST PROCESSING
# =============================================================================

def create_error_response(
    request_id: Optional[int | str],
    code: int,
    message: str,
    data: Optional[dict[str, Any]] = None,
) -> JSONRPCResponse:
    """Create a JSON-RPC error response."""
    return JSONRPCResponse(
        id=request_id,
        error=JSONRPCError(code=code, message=message, data=data),
    )


def create_success_response(
    request_id: Optional[int | str],
    result: dict[str, Any],
) -> JSONRPCResponse:
    """Create a JSON-RPC success response."""
    return JSONRPCResponse(
        id=request_id,
        result=result,
    )


async def process_jsonrpc_request(
    rpc_request: JSONRPCRequest,
    agent: AuthenticatedAgent,
    request: Request,
    session: Optional[MCPSession],
) -> tuple[JSONRPCResponse, Optional[str]]:
    """
    Process a single JSON-RPC request.

    Args:
        rpc_request: Parsed JSON-RPC request
        agent: Authenticated agent
        request: FastAPI request
        session: Current MCP session (may be None for initialize)

    Returns:
        Tuple of (response, new_session_id or None)
    """
    method = rpc_request.method
    params = rpc_request.params
    request_id = rpc_request.id

    new_session_id = None

    try:
        if method == "initialize":
            result, new_session_id = await handle_initialize(params, agent, request)

        elif method == "tools/list":
            result = await handle_tools_list(params, agent)

        elif method == "tools/call":
            if params is None:
                return create_error_response(
                    request_id,
                    JSONRPCErrorCodes.INVALID_PARAMS,
                    "Missing params for tools/call",
                ), None
            result = await handle_tools_call(params, agent, session)

        elif method == "notifications/initialized":
            # Client acknowledgment - no response needed for notifications
            # But we'll return a success for non-notification style calls
            result = {"acknowledged": True}

        elif method == "ping":
            # Simple ping/pong
            result = {"pong": True, "timestamp": datetime.now(timezone.utc).isoformat()}

        else:
            return create_error_response(
                request_id,
                JSONRPCErrorCodes.METHOD_NOT_FOUND,
                f"Method not found: {method}",
            ), None

        return create_success_response(request_id, result), new_session_id

    except ValueError as e:
        return create_error_response(
            request_id,
            JSONRPCErrorCodes.INVALID_PARAMS,
            str(e),
        ), None

    except HTTPException as e:
        return create_error_response(
            request_id,
            JSONRPCErrorCodes.SERVER_ERROR,
            e.detail,
            {"status_code": e.status_code},
        ), None

    except Exception as e:
        logger.exception(f"Error processing {method}: {e}")
        return create_error_response(
            request_id,
            JSONRPCErrorCodes.INTERNAL_ERROR,
            f"Internal error: {type(e).__name__}",
        ), None


# =============================================================================
# HTTP ENDPOINTS
# =============================================================================

@router.post(
    "",
    summary="Process MCP JSON-RPC request",
    description="""
Process a JSON-RPC request for MCP protocol.

Supported methods:
- `initialize`: Create MCP session, returns protocol info
- `tools/list`: List available tools
- `tools/call`: Execute a tool

Headers:
- `Mcp-Session-Id`: Session identifier (returned from initialize)
- `Authorization`: Bearer token for authentication
- `Accept`: application/json or text/event-stream

Response includes `Mcp-Session-Id` header for session tracking.
    """,
    responses={
        200: {"description": "JSON-RPC response"},
        400: {"description": "Invalid JSON-RPC request"},
        401: {"description": "Authentication required"},
        403: {"description": "Access denied (origin validation)"},
        406: {"description": "Not acceptable (content type)"},
        429: {"description": "Session limit exceeded"},
    },
)
async def mcp_post(
    request: Request,
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """
    POST /mcp - Process JSON-RPC request.

    Main entry point for MCP HTTP transport.
    """
    # Validate security headers
    validate_origin(request)
    content_type = validate_accept_header(request)

    # Parse request body
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return JSONResponse(
            status_code=200,  # JSON-RPC errors use 200
            content=create_error_response(
                None,
                JSONRPCErrorCodes.PARSE_ERROR,
                f"Invalid JSON: {e.msg}",
            ).to_dict(),
        )

    # Get or validate existing session
    # CRITICAL-1 FIX: Use async-safe session store initialization
    session_store = await get_session_store_async()
    session: Optional[MCPSession] = None
    session_id = request.headers.get("Mcp-Session-Id")

    if session_id:
        try:
            session = await session_store.validate(session_id, agent.agent_id)
        except SessionNotFoundError:
            # HIGH-3 FIX: Include session ID header in error responses
            response = JSONResponse(
                status_code=404,
                content={"detail": f"Session {session_id} not found or expired"},
            )
            response.headers["Mcp-Session-Id"] = session_id
            return response
        except SessionOwnershipError:
            # HIGH-3 FIX: Include session ID header in error responses
            response = JSONResponse(
                status_code=403,
                content={"detail": "Session belongs to another agent"},
            )
            response.headers["Mcp-Session-Id"] = session_id
            return response

    # Handle batched requests (array) or single request (object)
    if isinstance(body, list):
        # Batched JSON-RPC requests
        responses = []
        new_session_id = None

        for item in body:
            try:
                rpc_request = JSONRPCRequest(**item)
            except Exception as e:
                responses.append(create_error_response(
                    item.get("id") if isinstance(item, dict) else None,
                    JSONRPCErrorCodes.INVALID_REQUEST,
                    f"Invalid request: {str(e)}",
                ).to_dict())
                continue

            response, created_session_id = await process_jsonrpc_request(
                rpc_request, agent, request, session
            )

            # Track session ID from initialize
            if created_session_id:
                new_session_id = created_session_id

            # Only include responses with IDs (notifications don't get responses)
            if rpc_request.id is not None:
                responses.append(response.to_dict())

        # Return batched response
        # MEDIUM-3 FIX: Always return array for batch requests per JSON-RPC 2.0 spec
        http_response = JSONResponse(
            status_code=200,
            content=responses,  # Always return array, even if single item or empty
        )

        if new_session_id:
            http_response.headers["Mcp-Session-Id"] = new_session_id
        elif session_id:
            http_response.headers["Mcp-Session-Id"] = session_id

        return http_response

    else:
        # Single JSON-RPC request
        try:
            rpc_request = JSONRPCRequest(**body)
        except Exception as e:
            return JSONResponse(
                status_code=200,
                content=create_error_response(
                    body.get("id") if isinstance(body, dict) else None,
                    JSONRPCErrorCodes.INVALID_REQUEST,
                    f"Invalid request: {str(e)}",
                ).to_dict(),
            )

        response, new_session_id = await process_jsonrpc_request(
            rpc_request, agent, request, session
        )

        http_response = JSONResponse(
            status_code=200,
            content=response.to_dict(),
        )

        # Set session ID header
        if new_session_id:
            http_response.headers["Mcp-Session-Id"] = new_session_id
        elif session_id:
            http_response.headers["Mcp-Session-Id"] = session_id

        return http_response


@router.get(
    "",
    summary="SSE stream for server-initiated messages",
    description="""
Establish SSE stream for server-initiated messages.

This endpoint is used for:
- Real-time notifications (new messages, handoffs)
- Streaming tool responses (progress events)
- Heartbeat keep-alive (every 30 seconds)

Requires `Mcp-Session-Id` header from previous initialize call.
Optionally accepts `Last-Event-ID` header for resumption.

Event types:
- `system/connected`: Initial connection acknowledgment
- `heartbeat`: Keep-alive ping (every 30 seconds)
- `message/received`: New message in inbox
- `message/broadcast`: Broadcast message received
- `knowledge/created`: New knowledge entry created
- `session/handoff`: Session handoff request
- `system/shutdown`: Server shutting down
    """,
    responses={
        200: {"description": "SSE stream established", "content": {"text/event-stream": {}}},
        400: {"description": "Missing session ID"},
        401: {"description": "Authentication required"},
        403: {"description": "Session belongs to another agent"},
        404: {"description": "Session not found"},
    },
)
async def mcp_get_sse(
    request: Request,
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """
    GET /mcp - SSE stream for server-initiated messages.

    Phase 2.5 implementation with notification bus integration.
    """
    from fastapi.responses import StreamingResponse
    from ..notification_bus import get_notification_bus

    # Validate origin and accept headers
    validate_origin(request)

    # Require session ID
    session_id = request.headers.get("Mcp-Session-Id")
    if not session_id:
        return JSONResponse(
            status_code=400,
            content={"error": "Mcp-Session-Id header required for SSE stream"},
        )

    # Validate session ownership
    # CRITICAL-1 FIX: Use async-safe session store initialization
    session_store = await get_session_store_async()
    try:
        session = await session_store.validate(session_id, agent.agent_id)
    except SessionNotFoundError:
        # HIGH-3 FIX: Include session ID header in error responses
        response = JSONResponse(
            status_code=404,
            content={"error": f"Session {session_id} not found or expired"},
        )
        response.headers["Mcp-Session-Id"] = session_id
        return response
    except SessionOwnershipError:
        # HIGH-3 FIX: Include session ID header in error responses
        response = JSONResponse(
            status_code=403,
            content={"error": "Session belongs to another agent"},
        )
        response.headers["Mcp-Session-Id"] = session_id
        return response

    # Get Last-Event-ID for resumption
    last_event_id = request.headers.get("Last-Event-ID")

    # Create SSE generator
    async def event_generator():
        """Generate SSE events from notification bus."""
        bus = await get_notification_bus()
        try:
            async for event in bus.subscribe(session_id, agent.agent_id, last_event_id):
                yield event.to_sse()
        except asyncio.CancelledError:
            logger.info(f"SSE stream cancelled for session {session_id}")
        except Exception as e:
            logger.error(f"SSE stream error for session {session_id}: {e}")
            # Send error event before closing
            error_event = f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            yield error_event

    logger.info(f"SSE stream established: session={session_id}, agent={agent.agent_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
            "Mcp-Session-Id": session_id,
        },
    )


@router.delete(
    "",
    summary="Terminate MCP session",
    description="""
Explicitly terminate an MCP session.

Requires `Mcp-Session-Id` header identifying the session to terminate.
Only the owning agent can terminate their session.

**Note:** Phase 2 implementation - currently returns 501.
    """,
    responses={
        200: {"description": "Session terminated"},
        401: {"description": "Authentication required"},
        403: {"description": "Cannot terminate another agent's session"},
        404: {"description": "Session not found"},
        501: {"description": "Not implemented (Phase 2)"},
    },
)
async def mcp_delete(
    request: Request,
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """
    DELETE /mcp - Terminate session.

    Phase 2 implementation.
    """
    session_id = request.headers.get("Mcp-Session-Id")

    if not session_id:
        return JSONResponse(
            status_code=400,
            content={"error": "Mcp-Session-Id header required"},
        )

    # Phase 2: Full implementation
    # CRITICAL-1 FIX: Use async-safe session store initialization
    session_store = await get_session_store_async()

    try:
        terminated = await session_store.terminate(session_id, agent.agent_id)
        if terminated:
            return JSONResponse(
                status_code=200,
                content={"success": True, "session_id": session_id, "terminated": True},
            )
        else:
            return JSONResponse(
                status_code=404,
                content={"error": f"Session {session_id} not found"},
            )
    except SessionOwnershipError as e:
        return JSONResponse(
            status_code=403,
            content={"error": str(e)},
        )
