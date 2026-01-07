"""
DAKB MCP Server Implementation

MCP (Model Context Protocol) server that exposes DAKB functionality
to Claude Code agents through the stdio transport.

Version: 1.2
Created: 2025-12-08
Updated: 2025-12-09
Author: Backend Agent (Claude Opus 4.5)

Changelog:
    v1.2 (2025-12-09):
        - Added profile-based tool loading system (standard/full)
        - Standard profile: 12 tools (~10k tokens, ~46% reduction)
        - Full profile: 28 tools (~18.5k tokens)
        - Use DAKB_PROFILE environment variable to select profile

    v1.1 (2025-12-08):
        - ISS-058: Fixed CPU spinning bug when client disconnects
        - Added consecutive empty read detection to gracefully exit on EOF
        - Added asyncio.sleep(0.1) delay to prevent CPU spin during temp empty reads
        - Server now properly exits when stdin is closed instead of looping forever

Usage:
    # As a module (standard profile - default)
    python -m backend.dakb_service.mcp.server

    # With full profile (all 28 tools)
    DAKB_PROFILE=full python -m backend.dakb_service.mcp.server

    # Or directly
    python backend/dakb_service/mcp/server.py

    # With authentication token
    DAKB_AUTH_TOKEN=<token> python -m backend.dakb_service.mcp.server

Environment Variables:
    DAKB_GATEWAY_URL: Gateway URL (default: http://localhost:3100)
    DAKB_AUTH_TOKEN: Pre-configured authentication token
    DAKB_CLIENT_TIMEOUT: Request timeout in seconds (default: 30)
    DAKB_LOG_LEVEL: Logging level (default: INFO)
    DAKB_PROFILE: Tool profile to load - 'standard' or 'full' (default: standard)
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

# Configure logging
log_level = os.getenv("DAKB_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,  # MCP uses stdout for protocol, stderr for logs
)
logger = logging.getLogger(__name__)

from .handlers import ToolResponse, dispatch_tool, set_client_token
from .tools import (
    PROFILE_FULL,
    PROFILE_STANDARD,
    get_tools_by_profile,
    validate_tool_args,
)

# =============================================================================
# MCP SERVER (STDIO TRANSPORT)
# =============================================================================

class DAKBMCPServer:
    """
    MCP Server for DAKB integration.

    Implements the Model Context Protocol using stdio transport,
    allowing Claude Code to call DAKB tools.

    Protocol:
    - Input: JSON-RPC 2.0 messages via stdin
    - Output: JSON-RPC 2.0 responses via stdout
    - Logs: Written to stderr
    """

    # Server metadata
    SERVER_NAME = "dakb-mcp-server"
    SERVER_VERSION = "1.2.0"
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self):
        """Initialize the MCP server."""
        self._running = False
        self._auth_token: str | None = os.getenv("DAKB_AUTH_TOKEN")
        self._consecutive_empty_reads = 0  # ISS-058: Track empty reads to detect disconnect
        self._max_empty_reads = 10  # Exit after this many consecutive empty reads
        # ISS-057 Fix: Token is set asynchronously during initialize
        # Don't call async function from __init__

        # Profile-based tool loading (v1.2)
        profile_env = os.getenv("DAKB_PROFILE", PROFILE_STANDARD).lower()
        if profile_env not in (PROFILE_STANDARD, PROFILE_FULL):
            logger.warning(
                f"Invalid DAKB_PROFILE '{profile_env}', defaulting to '{PROFILE_STANDARD}'. "
                f"Valid options: '{PROFILE_STANDARD}', '{PROFILE_FULL}'"
            )
            profile_env = PROFILE_STANDARD
        self._profile: str = profile_env
        self._tools = get_tools_by_profile(self._profile)
        logger.info(f"Loaded profile: {self._profile} ({len(self._tools)} tools)")

    # -------------------------------------------------------------------------
    # Protocol Message Handlers
    # -------------------------------------------------------------------------

    async def handle_initialize(
        self,
        params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Handle initialize request.

        Called when Claude Code connects to the server.

        Args:
            params: Initialization parameters from client.

        Returns:
            Server capabilities and metadata.
        """
        logger.info(f"Initialize request from client: {params.get('clientInfo', {})}")

        # ISS-057 Fix: Set auth token asynchronously during initialize
        if self._auth_token:
            await set_client_token(self._auth_token)
            logger.info("Pre-configured auth token applied")

        return {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {
                "tools": {},  # We support tools
                "resources": {},  # We could add resources later
            },
            "serverInfo": {
                "name": self.SERVER_NAME,
                "version": self.SERVER_VERSION,
            },
        }

    async def handle_list_tools(self) -> dict[str, Any]:
        """
        Handle tools/list request.

        Returns the list of available DAKB tools based on the configured profile.
        - Standard profile: Core tools + 1 proxy for advanced operations
        - Full profile: All DAKB tools (no proxy needed)

        Returns:
            Tool definitions.
        """
        logger.debug(f"Listing available tools (profile: {self._profile})")

        return {
            "tools": self._tools,
        }

    async def handle_call_tool(
        self,
        params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Handle tools/call request.

        Executes the requested tool with provided arguments.

        Args:
            params: Tool call parameters (name and arguments).

        Returns:
            Tool execution result.
        """
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})

        logger.info(f"Tool call: {tool_name}")
        logger.debug(f"Tool arguments: {tool_args}")

        # Validate tool name
        if not tool_name:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": "Tool name is required",
                            "error_code": "MISSING_TOOL_NAME",
                        }),
                    }
                ],
                "isError": True,
            }

        # Validate arguments
        is_valid, error_msg = validate_tool_args(tool_name, tool_args)
        if not is_valid:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": error_msg,
                            "error_code": "VALIDATION_ERROR",
                        }),
                    }
                ],
                "isError": True,
            }

        # Execute tool
        try:
            response: ToolResponse = await dispatch_tool(tool_name, tool_args)

            return {
                "content": response.to_mcp_content(),
                "isError": not response.success,
            }

        except Exception as e:
            logger.exception(f"Tool execution error: {e}")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": f"Tool execution failed: {str(e)}",
                            "error_code": "EXECUTION_ERROR",
                        }),
                    }
                ],
                "isError": True,
            }

    async def handle_set_token(
        self,
        params: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Handle custom dakb/setToken request.

        Allows setting the authentication token at runtime.

        Args:
            params: Token parameters.

        Returns:
            Success/failure response.
        """
        token = params.get("token")
        if not token:
            return {"success": False, "error": "Token is required"}

        await set_client_token(token)
        self._auth_token = token
        logger.info("Authentication token updated")

        return {"success": True}

    # -------------------------------------------------------------------------
    # Message Routing
    # -------------------------------------------------------------------------

    async def handle_message(
        self,
        message: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Route incoming MCP message to appropriate handler.

        Args:
            message: Parsed JSON-RPC message.

        Returns:
            Response message or None for notifications.
        """
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params", {})

        logger.debug(f"Received method: {method}")

        # Route to handler
        try:
            if method == "initialize":
                result = await self.handle_initialize(params)
            elif method == "tools/list":
                result = await self.handle_list_tools()
            elif method == "tools/call":
                result = await self.handle_call_tool(params)
            elif method == "dakb/setToken":
                result = await self.handle_set_token(params)
            elif method == "notifications/initialized":
                # Notification - no response needed
                logger.info("Client initialized notification received")
                return None
            elif method == "ping":
                result = {}  # Simple ping/pong
            else:
                logger.warning(f"Unknown method: {method}")
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                }

            # Build success response
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": result,
            }

        except Exception as e:
            logger.exception(f"Handler error for {method}: {e}")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}",
                },
            }

    # -------------------------------------------------------------------------
    # Server Loop
    # -------------------------------------------------------------------------

    async def read_message(self) -> dict[str, Any] | None:
        """
        Read a JSON-RPC message from stdin.

        MCP uses newline-delimited JSON for message framing.

        Returns:
            Parsed message or None on EOF.
        """
        loop = asyncio.get_event_loop()

        try:
            # Read line from stdin asynchronously
            line = await loop.run_in_executor(None, sys.stdin.readline)

            if not line:
                return None

            line = line.strip()
            if not line:
                return None

            return json.loads(line)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Read error: {e}")
            return None

    def write_message(self, message: dict[str, Any]) -> None:
        """
        Write a JSON-RPC message to stdout.

        Args:
            message: Message to send.
        """
        try:
            output = json.dumps(message)
            print(output, flush=True)  # Print to stdout with newline
        except Exception as e:
            logger.error(f"Write error: {e}")

    async def run(self) -> None:
        """
        Main server loop.

        Reads messages from stdin, processes them, and writes
        responses to stdout.

        ISS-058 Fix: Properly detect client disconnect to prevent CPU spinning.
        When stdin is closed, readline() returns empty string immediately,
        causing an infinite loop. We track consecutive empty reads and exit
        gracefully after a threshold is reached.
        """
        logger.info(f"DAKB MCP Server v{self.SERVER_VERSION} starting")
        logger.info(f"Protocol version: {self.PROTOCOL_VERSION}")
        logger.info(f"Tool profile: {self._profile} ({len(self._tools)} tools available)")

        self._running = True

        while self._running:
            try:
                # Read incoming message
                message = await self.read_message()

                if message is None:
                    # ISS-058: Track consecutive empty reads to detect disconnect
                    self._consecutive_empty_reads += 1

                    if self._consecutive_empty_reads >= self._max_empty_reads:
                        # Client has disconnected (stdin closed)
                        logger.info(
                            f"Client disconnected after {self._consecutive_empty_reads} "
                            "consecutive empty reads. Shutting down gracefully."
                        )
                        break

                    # Small delay to prevent CPU spinning during temporary empty reads
                    await asyncio.sleep(0.1)
                    continue

                # Valid message received - reset empty read counter
                self._consecutive_empty_reads = 0

                # Handle message
                response = await self.handle_message(message)

                # Send response if not a notification
                if response is not None:
                    self.write_message(response)

            except KeyboardInterrupt:
                logger.info("Server interrupted")
                break
            except Exception as e:
                logger.exception(f"Server loop error: {e}")

        logger.info("Server stopped")

    def stop(self) -> None:
        """Stop the server."""
        self._running = False


# =============================================================================
# RESOURCES (Optional - for future extension)
# =============================================================================

class DAKBResources:
    """
    MCP Resources for DAKB.

    Provides read-only access to DAKB resources through
    the MCP resource protocol.
    """

    RESOURCES = [
        {
            "uri": "dakb://status",
            "mimeType": "application/json",
            "name": "DAKB System Status",
            "description": "Current status of DAKB services",
        },
        {
            "uri": "dakb://stats",
            "mimeType": "application/json",
            "name": "DAKB Statistics",
            "description": "Knowledge base statistics",
        },
    ]

    @staticmethod
    def list_resources() -> list[dict[str, Any]]:
        """List available resources."""
        return DAKBResources.RESOURCES

    @staticmethod
    async def read_resource(uri: str) -> dict[str, Any]:
        """Read a resource by URI."""
        from .handlers import handle_status

        if uri == "dakb://status":
            result = await handle_status({})
            return {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(result.to_dict(), indent=2),
            }

        raise ValueError(f"Unknown resource: {uri}")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main() -> None:
    """Main entry point for the MCP server."""
    server = DAKBMCPServer()

    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    except Exception as e:
        logger.exception(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
