"""
DAKB Local Proxy Server

Main proxy server that bridges MCP stdio clients to DAKB HTTP gateway.
Provides local caching and connection management.

Version: 1.0.1
Created: 2025-12-17
Updated: 2025-12-17 - Fixed asyncio API, added retry logic, HTTP status validation
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Optional

import httpx

from .config import ProxyConfig
from .cache import LocalCache, SearchCache, KnowledgeCache

logger = logging.getLogger(__name__)

# Retry configuration constants
RETRY_BACKOFF_BASE = 0.5  # Base delay in seconds
RETRY_BACKOFF_MAX = 10.0  # Maximum delay in seconds


class DAKBLocalProxy:
    """
    Local proxy server for DAKB.

    Bridges MCP stdio transport to DAKB HTTP gateway with caching.

    Features:
    - MCP stdio input/output handling
    - Local caching for search and knowledge
    - Connection pooling to gateway
    - Retry logic with backoff

    Usage:
        config = ProxyConfig.load()
        proxy = DAKBLocalProxy(config)
        await proxy.run()
    """

    def __init__(self, config: ProxyConfig):
        """
        Initialize proxy.

        Args:
            config: Proxy configuration
        """
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._running = False
        self._request_id = 0

        # Initialize caches
        self._search_cache = SearchCache(
            max_entries=config.cache.max_entries // 2,
            default_ttl=config.cache.search_cache_ttl,
        )
        self._knowledge_cache = KnowledgeCache(
            max_entries=config.cache.max_entries // 2,
            default_ttl=config.cache.ttl_seconds,
        )

        # MCP session tracking
        self._mcp_session_id: Optional[str] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            headers = {
                "Authorization": f"Bearer {self.config.auth_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            # Add MCP session ID if available
            if self._mcp_session_id:
                headers["Mcp-Session-Id"] = self._mcp_session_id

            self._client = httpx.AsyncClient(
                base_url=self.config.connection.gateway_url,
                timeout=httpx.Timeout(self.config.connection.timeout_seconds),
                headers=headers,
                limits=httpx.Limits(
                    max_keepalive_connections=self.config.connection.keepalive_connections,
                    max_connections=self.config.connection.keepalive_connections * 2,
                ),
            )
        return self._client

    async def _request_with_retry(
        self,
        rpc_request: dict[str, Any],
        endpoint: str = "/mcp",
    ) -> dict[str, Any]:
        """
        Make HTTP request with retry logic and status validation.

        Args:
            rpc_request: JSON-RPC request body
            endpoint: API endpoint to call

        Returns:
            Parsed JSON response data

        Raises:
            Exception: If all retries fail or response is invalid
        """
        max_retries = self.config.connection.max_retries
        last_exception: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                client = await self._get_client()
                response = await client.post(endpoint, json=rpc_request)

                # Validate HTTP status before parsing JSON
                if response.status_code >= 400:
                    error_text = response.text[:500]  # Limit error text length
                    if response.status_code >= 500:
                        # Server error - retry
                        raise httpx.HTTPStatusError(
                            f"Server error {response.status_code}: {error_text}",
                            request=response.request,
                            response=response,
                        )
                    else:
                        # Client error - don't retry
                        raise Exception(
                            f"Client error {response.status_code}: {error_text}"
                        )

                # Parse JSON response
                try:
                    data = response.json()
                except json.JSONDecodeError as e:
                    raise Exception(f"Invalid JSON response: {e}")

                # Track MCP session ID from response headers
                if "Mcp-Session-Id" in response.headers:
                    new_session_id = response.headers["Mcp-Session-Id"]
                    if self._mcp_session_id != new_session_id:
                        self._mcp_session_id = new_session_id
                        logger.debug(f"MCP session: {new_session_id[:16]}...")

                return data

            except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    # Calculate exponential backoff delay
                    delay = min(
                        RETRY_BACKOFF_BASE * (2 ** attempt),
                        RETRY_BACKOFF_MAX,
                    )
                    logger.warning(
                        f"Request failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    # Reset client on connection errors
                    if isinstance(e, httpx.ConnectError):
                        if self._client and not self._client.is_closed:
                            await self._client.aclose()
                        self._client = None
                else:
                    logger.error(f"All {max_retries} retries failed: {e}")

        raise Exception(f"Request failed after {max_retries} retries: {last_exception}")

    async def close(self) -> None:
        """Close proxy and release resources."""
        self._running = False
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

        # Save cache if persistence enabled
        if self.config.cache.persist_to_disk:
            self._search_cache.save_to_disk()
            self._knowledge_cache.save_to_disk()

        logger.info("Proxy closed")

    async def run(self) -> None:
        """
        Run the proxy server.

        Listens on stdin for JSON-RPC requests and writes responses to stdout.
        """
        self._running = True
        logger.info(f"Starting DAKB local proxy (gateway: {self.config.connection.gateway_url})")

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)

        # Connect stdin to reader - use get_running_loop() for Python 3.10+ compatibility
        loop = asyncio.get_running_loop()
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        # Create stdout writer
        write_transport, _ = await loop.connect_write_pipe(
            lambda: asyncio.Protocol(),
            sys.stdout,
        )
        writer = asyncio.StreamWriter(write_transport, protocol, reader, loop)

        logger.info("Proxy ready for MCP stdio transport")

        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    break

                try:
                    request = json.loads(line.decode().strip())
                    response = await self._handle_request(request)
                    if response:
                        writer.write((json.dumps(response) + "\n").encode())
                        await writer.drain()
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {e}")
                except Exception as e:
                    logger.exception(f"Error handling request: {e}")
                    error_response = self._make_error_response(
                        request.get("id") if isinstance(request, dict) else None,
                        -32603,
                        str(e),
                    )
                    writer.write((json.dumps(error_response) + "\n").encode())
                    await writer.drain()

        except asyncio.CancelledError:
            logger.info("Proxy cancelled")
        finally:
            await self.close()

    async def _handle_request(self, request: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        Handle incoming JSON-RPC request.

        Args:
            request: JSON-RPC request

        Returns:
            JSON-RPC response or None for notifications
        """
        method = request.get("method", "")
        params = request.get("params", {})
        request_id = request.get("id")

        logger.debug(f"Handling: {method}")

        try:
            if method == "initialize":
                result = await self._handle_initialize(params)
            elif method == "tools/list":
                result = await self._handle_tools_list(params)
            elif method == "tools/call":
                result = await self._handle_tools_call(params)
            elif method == "notifications/initialized":
                # Notification - no response
                return None
            elif method == "ping":
                result = {"pong": True}
            else:
                return self._make_error_response(
                    request_id,
                    -32601,
                    f"Method not found: {method}",
                )

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }

        except Exception as e:
            logger.exception(f"Error in {method}: {e}")
            return self._make_error_response(request_id, -32603, str(e))

    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle initialize request."""
        rpc_request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": params,
        }

        # Use retry-enabled request method
        data = await self._request_with_retry(rpc_request)

        if "error" in data:
            raise Exception(data["error"].get("message", "Initialize failed"))

        return data.get("result", {})

    async def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tools/list request."""
        rpc_request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": params,
        }

        # Use retry-enabled request method
        data = await self._request_with_retry(rpc_request)

        if "error" in data:
            raise Exception(data["error"].get("message", "tools/list failed"))

        return data.get("result", {})

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Handle tools/call request with caching.

        Caches search and get_knowledge results locally.
        """
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Check cache for search
        if tool_name == "dakb_search" and self.config.cache.enabled:
            cached = self._search_cache.get_search(
                query=arguments.get("query", ""),
                limit=arguments.get("limit", 5),
                min_score=arguments.get("min_score", 0.3),
                category=arguments.get("category"),
            )
            if cached:
                logger.debug(f"Cache hit for search: {arguments.get('query', '')[:30]}")
                return {
                    "content": [{"type": "text", "text": json.dumps(cached, indent=2)}],
                    "isError": False,
                    "_cached": True,
                }

        # Check cache for get_knowledge
        if tool_name == "dakb_get_knowledge" and self.config.cache.enabled:
            knowledge_id = arguments.get("knowledge_id", "")
            cached = self._knowledge_cache.get_knowledge(knowledge_id)
            if cached:
                logger.debug(f"Cache hit for knowledge: {knowledge_id}")
                return {
                    "content": [{"type": "text", "text": json.dumps(cached, indent=2)}],
                    "isError": False,
                    "_cached": True,
                }

        # Forward to gateway using retry-enabled request
        rpc_request = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": params,
        }

        data = await self._request_with_retry(rpc_request)

        if "error" in data:
            raise Exception(data["error"].get("message", "tools/call failed"))

        result = data.get("result", {})

        # Cache successful results
        if self.config.cache.enabled and not result.get("isError"):
            self._cache_result(tool_name, arguments, result)

        return result

    def _cache_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Cache tool result if applicable."""
        try:
            content = result.get("content", [])
            if not content or content[0].get("type") != "text":
                return

            data = json.loads(content[0].get("text", "{}"))

            if tool_name == "dakb_search":
                self._search_cache.set_search(
                    query=arguments.get("query", ""),
                    results=data,
                    limit=arguments.get("limit", 5),
                    min_score=arguments.get("min_score", 0.3),
                    category=arguments.get("category"),
                )
                logger.debug(f"Cached search: {arguments.get('query', '')[:30]}")

            elif tool_name == "dakb_get_knowledge":
                knowledge_id = arguments.get("knowledge_id", "")
                if knowledge_id:
                    self._knowledge_cache.set_knowledge(knowledge_id, data)
                    logger.debug(f"Cached knowledge: {knowledge_id}")

        except Exception as e:
            logger.debug(f"Cache save error: {e}")

    def _next_id(self) -> int:
        """Generate next request ID."""
        self._request_id += 1
        return self._request_id

    @staticmethod
    def _make_error_response(
        request_id: Optional[int | str],
        code: int,
        message: str,
    ) -> dict[str, Any]:
        """Create JSON-RPC error response."""
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    def cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "search_cache": self._search_cache.stats(),
            "knowledge_cache": self._knowledge_cache.stats(),
        }

    def clear_cache(self) -> dict[str, int]:
        """Clear all caches."""
        return {
            "search_cleared": self._search_cache.clear(),
            "knowledge_cleared": self._knowledge_cache.clear(),
        }


async def run_proxy(config: Optional[ProxyConfig] = None) -> None:
    """
    Run the proxy server.

    Args:
        config: Optional configuration override
    """
    if config is None:
        config = ProxyConfig.load()

    errors = config.validate()
    if errors:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    config.setup_logging()
    proxy = DAKBLocalProxy(config)

    try:
        await proxy.run()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        await proxy.close()
