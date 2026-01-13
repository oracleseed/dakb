"""
DAKB MCP HTTP Client

An MCP-native client for DAKB that uses the MCP HTTP transport (POST /mcp).
Implements full JSON-RPC 2.0 protocol for MCP 2025-03-26 specification.

Version: 1.0.0
Created: 2025-12-17

Usage:
    from dakb_client import DAKBMCPClient

    async with DAKBMCPClient(base_url="http://localhost:3100", token="your-token") as client:
        # Initialize session
        await client.initialize()

        # List available tools
        tools = await client.list_tools()

        # Call a tool
        result = await client.call_tool("dakb_search", {"query": "patterns"})

        # Subscribe to notifications
        async for event in client.subscribe():
            print(f"Received: {event}")
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional
from datetime import datetime, timezone

import httpx

from .exceptions import (
    DAKBError,
    DAKBConnectionError,
    DAKBSessionError,
    DAKBJSONRPCError,
    DAKBTimeoutError,
)

logger = logging.getLogger(__name__)


class DAKBMCPClient:
    """
    MCP HTTP transport client for DAKB.

    Implements the MCP 2025-03-26 Streamable HTTP protocol for direct
    communication with the DAKB MCP gateway.

    Args:
        base_url: DAKB gateway URL (e.g., "http://localhost:3100")
        token: Authentication token
        timeout: Request timeout in seconds (default: 30)
        verify_ssl: Verify SSL certificates (default: True)

    Example:
        async with DAKBMCPClient("http://localhost:3100", "my-token") as client:
            await client.initialize()
            tools = await client.list_tools()
            result = await client.call_tool("dakb_search", {"query": "patterns"})
    """

    PROTOCOL_VERSION = "2025-03-26"
    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = DEFAULT_TIMEOUT,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.verify_ssl = verify_ssl

        self._client: Optional[httpx.AsyncClient] = None
        self._session_id: Optional[str] = None
        self._request_id: int = 0
        self._initialized: bool = False
        self._capabilities: dict[str, Any] = {}
        self._server_info: dict[str, Any] = {}

    @property
    def session_id(self) -> Optional[str]:
        """Get current MCP session ID."""
        return self._session_id

    @property
    def is_initialized(self) -> bool:
        """Check if client is initialized."""
        return self._initialized

    @property
    def capabilities(self) -> dict[str, Any]:
        """Get server capabilities from initialize response."""
        return self._capabilities

    @property
    def server_info(self) -> dict[str, Any]:
        """Get server info from initialize response."""
        return self._server_info

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                verify=self.verify_ssl,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and terminate session."""
        if self._session_id:
            try:
                await self._terminate_session()
            except Exception as e:
                logger.warning(f"Error terminating session: {e}")

        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

        self._session_id = None
        self._initialized = False

    async def __aenter__(self) -> "DAKBMCPClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def __repr__(self) -> str:
        """String representation with masked token for security."""
        masked_token = f"{self.token[:4]}...{self.token[-4:]}" if len(self.token) > 8 else "****"
        return f"DAKBMCPClient(base_url={self.base_url!r}, token={masked_token!r}, session_id={self._session_id!r})"

    # =========================================================================
    # JSON-RPC HELPERS
    # =========================================================================

    def _next_request_id(self) -> int:
        """Generate next request ID."""
        self._request_id += 1
        return self._request_id

    def _build_request(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build JSON-RPC request object."""
        request = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": method,
        }
        if params is not None:
            request["params"] = params
        return request

    async def _send_request(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Send JSON-RPC request and handle response.

        Args:
            method: JSON-RPC method name
            params: Method parameters

        Returns:
            Result from JSON-RPC response

        Raises:
            DAKBJSONRPCError: If response contains error
            DAKBSessionError: If session is invalid
            DAKBConnectionError: If connection fails
        """
        client = await self._get_client()
        request = self._build_request(method, params)

        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            response = await client.post(
                "/mcp",
                json=request,
                headers=headers,
            )
        except httpx.ConnectError as e:
            raise DAKBConnectionError(f"Failed to connect: {e}")
        except httpx.TimeoutException as e:
            raise DAKBTimeoutError(
                f"Request timed out: {e}",
                timeout_seconds=self.timeout,
                operation=f"MCP {method}",
            )

        # Update session ID from response header
        new_session_id = response.headers.get("Mcp-Session-Id")
        if new_session_id:
            self._session_id = new_session_id

        # Handle HTTP errors
        if response.status_code == 404:
            raise DAKBSessionError(
                "Session not found or expired",
                session_id=self._session_id,
            )
        if response.status_code == 403:
            raise DAKBSessionError(
                "Session access denied",
                session_id=self._session_id,
            )

        try:
            data = response.json()
        except json.JSONDecodeError:
            raise DAKBError(f"Invalid JSON response: {response.text}")

        # Handle JSON-RPC error
        if "error" in data and data["error"]:
            error = data["error"]
            raise DAKBJSONRPCError(
                code=error.get("code", -32000),
                message=error.get("message", "Unknown error"),
                data=error.get("data"),
                request_id=data.get("id"),
            )

        return data.get("result", {})

    async def _terminate_session(self) -> None:
        """Terminate current MCP session via DELETE /mcp."""
        if not self._session_id:
            return

        client = await self._get_client()
        try:
            await client.delete(
                "/mcp",
                headers={"Mcp-Session-Id": self._session_id},
            )
        except Exception as e:
            logger.warning(f"Error during session termination: {e}")

    # =========================================================================
    # MCP PROTOCOL METHODS
    # =========================================================================

    async def initialize(
        self,
        client_info: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Initialize MCP session.

        This must be called before any other MCP operations.

        Args:
            client_info: Optional client information

        Returns:
            Initialize result with capabilities and server info

        Raises:
            DAKBJSONRPCError: If initialization fails
        """
        params = {}
        if client_info:
            params["clientInfo"] = client_info

        result = await self._send_request("initialize", params or None)

        self._initialized = True
        self._capabilities = result.get("capabilities", {})
        self._server_info = result.get("serverInfo", {})

        logger.info(
            f"MCP session initialized: {self._session_id}, "
            f"protocol: {result.get('protocolVersion')}"
        )

        # Send initialized notification
        await self._send_notification("notifications/initialized")

        return result

    async def _send_notification(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
    ) -> None:
        """Send JSON-RPC notification (no response expected)."""
        # Notifications don't have an ID
        request = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            request["params"] = params

        client = await self._get_client()
        headers = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        try:
            await client.post("/mcp", json=request, headers=headers)
        except Exception as e:
            logger.warning(f"Error sending notification {method}: {e}")

    async def list_tools(self) -> list[dict[str, Any]]:
        """
        List available tools.

        Returns:
            List of tool definitions with name, description, and inputSchema

        Raises:
            DAKBSessionError: If not initialized
        """
        if not self._initialized:
            raise DAKBSessionError(
                "Client not initialized. Call initialize() first.",
                reason="not_initialized",
            )

        result = await self._send_request("tools/list")
        return result.get("tools", [])

    async def call_tool(
        self,
        name: str,
        arguments: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Call a DAKB tool.

        Args:
            name: Tool name (e.g., "dakb_search", "dakb_store_knowledge")
            arguments: Tool arguments

        Returns:
            Tool result with content and isError flag

        Raises:
            DAKBSessionError: If not initialized
            DAKBJSONRPCError: If tool call fails

        Example:
            result = await client.call_tool("dakb_search", {
                "query": "error handling patterns",
                "limit": 5,
            })
        """
        if not self._initialized:
            raise DAKBSessionError(
                "Client not initialized. Call initialize() first.",
                reason="not_initialized",
            )

        params = {
            "name": name,
            "arguments": arguments or {},
        }

        result = await self._send_request("tools/call", params)

        # Parse tool result content
        if result.get("isError"):
            content = result.get("content", [])
            if content and content[0].get("type") == "text":
                try:
                    error_data = json.loads(content[0].get("text", "{}"))
                    raise DAKBError(
                        error_data.get("error", "Tool execution failed"),
                        details=error_data,
                    )
                except json.JSONDecodeError:
                    raise DAKBError(content[0].get("text", "Tool execution failed"))

        return result

    async def ping(self) -> bool:
        """
        Send ping to check connection.

        Returns:
            True if server responds with pong
        """
        try:
            result = await self._send_request("ping")
            return result.get("pong", False)
        except DAKBError:
            return False

    # =========================================================================
    # SSE SUBSCRIPTION
    # =========================================================================

    async def subscribe(
        self,
        last_event_id: Optional[str] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Subscribe to server-sent events for real-time notifications.

        Must be initialized first.

        Args:
            last_event_id: Last received event ID for resumption

        Yields:
            Event dictionaries with type, id, and data

        Example:
            async for event in client.subscribe():
                if event["type"] == "message/received":
                    print(f"New message: {event['data']}")
        """
        if not self._initialized or not self._session_id:
            raise DAKBSessionError(
                "Client not initialized. Call initialize() first.",
                reason="not_initialized",
            )

        client = await self._get_client()

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "text/event-stream",
            "Mcp-Session-Id": self._session_id,
        }
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id

        async with client.stream(
            "GET",
            "/mcp",
            headers=headers,
            timeout=None,  # SSE streams don't timeout
        ) as response:
            if response.status_code != 200:
                raise DAKBSessionError(
                    f"SSE connection failed: {response.status_code}",
                    session_id=self._session_id,
                )

            current_event: dict[str, Any] = {}
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current_event["type"] = line[6:].strip()
                elif line.startswith("data:"):
                    data_str = line[5:].strip()
                    try:
                        current_event["data"] = json.loads(data_str)
                    except json.JSONDecodeError:
                        current_event["data"] = data_str
                elif line.startswith("id:"):
                    current_event["id"] = line[3:].strip()
                elif line == "":
                    # Empty line = end of event
                    if current_event:
                        yield current_event
                        current_event = {}

    # =========================================================================
    # CONVENIENCE WRAPPERS
    # =========================================================================

    async def search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.3,
        category: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Search knowledge base (convenience wrapper).

        Args:
            query: Search query
            limit: Max results
            min_score: Minimum similarity score
            category: Filter by category

        Returns:
            Search results
        """
        args: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "min_score": min_score,
        }
        if category:
            args["category"] = category

        result = await self.call_tool("dakb_search", args)
        return self._parse_tool_content(result)

    async def store_knowledge(
        self,
        title: str,
        content: str,
        content_type: str,
        category: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Store knowledge entry (convenience wrapper).

        Args:
            title: Knowledge title
            content: Knowledge content
            content_type: Type (lesson_learned, pattern, etc.)
            category: Category (ml, backend, etc.)
            **kwargs: Additional arguments

        Returns:
            Created knowledge entry
        """
        args = {
            "title": title,
            "content": content,
            "content_type": content_type,
            "category": category,
            **kwargs,
        }
        result = await self.call_tool("dakb_store_knowledge", args)
        return self._parse_tool_content(result)

    async def send_message(
        self,
        recipient_id: str,
        subject: str,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Send message (convenience wrapper).

        Args:
            recipient_id: Target agent
            subject: Message subject
            content: Message body
            **kwargs: Additional arguments

        Returns:
            Sent message info
        """
        args = {
            "recipient_id": recipient_id,
            "subject": subject,
            "content": content,
            **kwargs,
        }
        result = await self.call_tool("dakb_send_message", args)
        return self._parse_tool_content(result)

    async def get_status(self) -> dict[str, Any]:
        """Get DAKB service status (convenience wrapper)."""
        result = await self.call_tool("dakb_status", {})
        return self._parse_tool_content(result)

    def _parse_tool_content(self, result: dict[str, Any]) -> dict[str, Any]:
        """Parse tool result content to dictionary."""
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            try:
                return json.loads(content[0].get("text", "{}"))
            except json.JSONDecodeError:
                return {"raw": content[0].get("text")}
        return result
