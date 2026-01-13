"""
DAKB Asynchronous Client

An asynchronous HTTP client for the DAKB REST API.
Uses httpx AsyncClient for non-blocking operations.

Version: 1.0.0
Created: 2025-12-17

Usage:
    from dakb_client import DAKBAsyncClient

    async with DAKBAsyncClient(base_url="http://localhost:3100", token="your-token") as client:
        # Search knowledge
        results = await client.search("machine learning patterns")

        # Store knowledge
        entry = await client.store_knowledge(
            title="PPO Training Tips",
            content="Always normalize your rewards...",
            content_type="lesson_learned",
            category="ml",
        )

        # Subscribe to notifications
        async for event in client.subscribe_notifications():
            print(f"Received: {event}")
"""

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional
from urllib.parse import urljoin

import httpx

from .exceptions import (
    DAKBError,
    DAKBConnectionError,
    DAKBAuthenticationError,
    DAKBNotFoundError,
    DAKBValidationError,
    DAKBRateLimitError,
    DAKBServerError,
    DAKBTimeoutError,
)
from .models import (
    ContentType,
    Category,
    AccessLevel,
    MessagePriority,
    VoteType,
)

logger = logging.getLogger(__name__)


class DAKBAsyncClient:
    """
    Asynchronous DAKB API client.

    Provides async methods for all DAKB operations including knowledge management,
    messaging, voting, session handling, and SSE subscription.

    Args:
        base_url: DAKB gateway URL (e.g., "http://localhost:3100")
        token: Authentication token
        timeout: Request timeout in seconds (default: 30)
        max_retries: Maximum retry attempts for failed requests (default: 3)
        verify_ssl: Verify SSL certificates (default: True)

    Example:
        async with DAKBAsyncClient("http://localhost:3100", "my-token") as client:
            results = await client.search("error handling patterns")
    """

    DEFAULT_TIMEOUT = 30.0
    MAX_RETRIES = 3
    API_VERSION = "v1"

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        verify_ssl: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_retries = max_retries
        self.verify_ssl = verify_ssl
        self._client: Optional[httpx.AsyncClient] = None

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
                limits=httpx.Limits(
                    max_keepalive_connections=10,
                    max_connections=20,
                ),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "DAKBAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # =========================================================================
    # HTTP HELPERS
    # =========================================================================

    def _api_url(self, path: str) -> str:
        """Build full API URL."""
        return f"/api/{self.API_VERSION}/{path.lstrip('/')}"

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """
        Handle HTTP response and raise appropriate exceptions.
        """
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {"raw_response": response.text}

        if response.status_code == 200:
            return data

        if response.status_code == 401:
            raise DAKBAuthenticationError(
                data.get("detail", "Authentication failed"),
                details=data,
            )

        if response.status_code == 403:
            raise DAKBAuthenticationError(
                data.get("detail", "Access denied"),
                details=data,
            )

        if response.status_code == 404:
            raise DAKBNotFoundError(
                data.get("detail", "Resource not found"),
            )

        if response.status_code in (400, 422):
            raise DAKBValidationError(
                data.get("detail", "Validation error"),
            )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise DAKBRateLimitError(
                data.get("detail", "Rate limit exceeded"),
                retry_after=int(retry_after) if retry_after else None,
            )

        if response.status_code >= 500:
            raise DAKBServerError(
                data.get("detail", "Server error"),
                status_code=response.status_code,
            )

        raise DAKBError(
            f"Unexpected status code: {response.status_code}",
            details=data,
        )

    async def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Make async HTTP request with retry logic.
        """
        client = await self._get_client()
        url = self._api_url(path)

        for attempt in range(self.max_retries):
            try:
                if method.upper() == "GET":
                    response = await client.get(url, params=params)
                elif method.upper() == "POST":
                    response = await client.post(url, json=data, params=params)
                elif method.upper() == "PUT":
                    response = await client.put(url, json=data, params=params)
                elif method.upper() == "PATCH":
                    response = await client.patch(url, json=data, params=params)
                elif method.upper() == "DELETE":
                    response = await client.delete(url, params=params)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                return self._handle_response(response)

            except httpx.ConnectError as e:
                if attempt == self.max_retries - 1:
                    raise DAKBConnectionError(
                        f"Failed to connect to DAKB service: {e}",
                        details={"url": url, "attempts": self.max_retries},
                    )
                logger.warning(f"Connection error (attempt {attempt + 1}): {e}")
                await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff

            except httpx.TimeoutException as e:
                if attempt == self.max_retries - 1:
                    raise DAKBTimeoutError(
                        f"Request timed out: {e}",
                        timeout_seconds=self.timeout,
                        operation=f"{method} {path}",
                    )
                logger.warning(f"Timeout (attempt {attempt + 1}): {e}")
                await asyncio.sleep(0.5 * (attempt + 1))

        # If we get here, all retries exhausted without specific exception
        raise DAKBError(
            f"Request failed after {self.max_retries} retries",
            code="RETRY_EXHAUSTED",
            details={"method": method, "path": path, "retries": self.max_retries},
        )

    def __repr__(self) -> str:
        """String representation with masked token for security."""
        masked_token = f"{self.token[:4]}...{self.token[-4:]}" if len(self.token) > 8 else "****"
        return f"DAKBAsyncClient(base_url={self.base_url!r}, token={masked_token!r})"

    # =========================================================================
    # KNOWLEDGE OPERATIONS
    # =========================================================================

    async def store_knowledge(
        self,
        title: str,
        content: str,
        content_type: str | ContentType,
        category: str | Category,
        confidence: float = 0.8,
        tags: Optional[list[str]] = None,
        related_files: Optional[list[str]] = None,
        access_level: str | AccessLevel = "public",
    ) -> dict[str, Any]:
        """Store new knowledge entry asynchronously."""
        data = {
            "title": title,
            "content": content,
            "content_type": content_type if isinstance(content_type, str) else content_type.value,
            "category": category if isinstance(category, str) else category.value,
            "confidence": confidence,
            "tags": tags or [],
            "related_files": related_files or [],
            "access_level": access_level if isinstance(access_level, str) else access_level.value,
        }
        return await self._request("POST", "knowledge", data=data)

    async def search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.3,
        category: Optional[str | Category] = None,
    ) -> dict[str, Any]:
        """Search knowledge base semantically."""
        params: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "min_score": min_score,
        }
        if category:
            params["category"] = category if isinstance(category, str) else category.value

        return await self._request("GET", "knowledge/search", params=params)

    async def get_knowledge(self, knowledge_id: str) -> dict[str, Any]:
        """Get full knowledge entry by ID."""
        return await self._request("GET", f"knowledge/{knowledge_id}")

    async def vote(
        self,
        knowledge_id: str,
        vote: str | VoteType,
        comment: Optional[str] = None,
        used_successfully: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Vote on knowledge quality."""
        data: dict[str, Any] = {
            "knowledge_id": knowledge_id,
            "vote": vote if isinstance(vote, str) else vote.value,
        }
        if comment:
            data["comment"] = comment
        if used_successfully is not None:
            data["used_successfully"] = used_successfully

        return await self._request("POST", "knowledge/vote", data=data)

    # =========================================================================
    # MESSAGING OPERATIONS
    # =========================================================================

    async def send_message(
        self,
        recipient_id: str,
        subject: str,
        content: str,
        priority: str | MessagePriority = "normal",
        thread_id: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        expires_in_hours: int = 168,
    ) -> dict[str, Any]:
        """Send direct message to another agent."""
        data: dict[str, Any] = {
            "recipient_id": recipient_id,
            "subject": subject,
            "content": content,
            "priority": priority if isinstance(priority, str) else priority.value,
            "expires_in_hours": expires_in_hours,
        }
        if thread_id:
            data["thread_id"] = thread_id
        if reply_to_id:
            data["reply_to_id"] = reply_to_id

        return await self._request("POST", "messages", data=data)

    async def get_messages(
        self,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        sender_id: Optional[str] = None,
        include_broadcasts: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Get messages from inbox."""
        params: dict[str, Any] = {
            "include_broadcasts": include_broadcasts,
            "page": page,
            "page_size": page_size,
        }
        if status:
            params["status"] = status
        if priority:
            params["priority"] = priority
        if sender_id:
            params["sender_id"] = sender_id

        return await self._request("GET", "messages", params=params)

    async def mark_read(
        self,
        message_id: Optional[str] = None,
        message_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Mark message(s) as read."""
        data: dict[str, Any] = {}
        if message_id:
            data["message_id"] = message_id
        if message_ids:
            data["message_ids"] = message_ids

        return await self._request("POST", "messages/mark-read", data=data)

    async def broadcast(
        self,
        subject: str,
        content: str,
        priority: str | MessagePriority = "normal",
        expires_in_hours: int = 168,
    ) -> dict[str, Any]:
        """Send broadcast message to all agents."""
        data = {
            "subject": subject,
            "content": content,
            "priority": priority if isinstance(priority, str) else priority.value,
            "expires_in_hours": expires_in_hours,
        }
        return await self._request("POST", "messages/broadcast", data=data)

    async def get_message_stats(self) -> dict[str, Any]:
        """Get messaging statistics for current agent."""
        return await self._request("GET", "messages/stats")

    # =========================================================================
    # STATUS AND STATS
    # =========================================================================

    async def status(self) -> dict[str, Any]:
        """Check DAKB service health status."""
        return await self._request("GET", "status")

    async def get_stats(self) -> dict[str, Any]:
        """Get knowledge base statistics."""
        return await self._request("GET", "stats")

    # =========================================================================
    # ADVANCED OPERATIONS
    # =========================================================================

    async def advanced(
        self,
        operation: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Execute advanced DAKB operation via proxy."""
        data = {
            "operation": operation,
            "params": params or {},
        }
        return await self._request("POST", "advanced", data=data)

    # =========================================================================
    # SSE SUBSCRIPTION
    # =========================================================================

    async def subscribe_notifications(
        self,
        session_id: str,
        last_event_id: Optional[str] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Subscribe to server-sent events for real-time notifications.

        Args:
            session_id: MCP session ID (from initialize)
            last_event_id: Last received event ID for resumption

        Yields:
            Event dictionaries with type and data

        Example:
            async for event in client.subscribe_notifications("mcp_abc123"):
                if event["type"] == "message/received":
                    print(f"New message: {event['data']}")
        """
        client = await self._get_client()

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "text/event-stream",
            "Mcp-Session-Id": session_id,
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
                raise DAKBError(
                    f"SSE connection failed: {response.status_code}",
                    details={"session_id": session_id},
                )

            current_event = {}
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
    # CONVENIENCE METHODS
    # =========================================================================

    async def ping(self) -> bool:
        """Quick health check."""
        try:
            status = await self.status()
            return status.get("success", False) or status.get("gateway_status") == "ok"
        except DAKBError:
            return False

    async def store_lesson_learned(
        self,
        title: str,
        content: str,
        category: str | Category,
        tags: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Convenience method to store a lesson learned."""
        return await self.store_knowledge(
            title=title,
            content=content,
            content_type=ContentType.LESSON_LEARNED,
            category=category,
            tags=tags,
        )

    async def store_error_fix(
        self,
        title: str,
        content: str,
        category: str | Category,
        related_files: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Convenience method to store an error fix."""
        return await self.store_knowledge(
            title=title,
            content=content,
            content_type=ContentType.ERROR_FIX,
            category=category,
            related_files=related_files,
        )
