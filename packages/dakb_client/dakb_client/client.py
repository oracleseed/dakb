"""
DAKB Synchronous Client

A synchronous HTTP client for the DAKB REST API.
Uses httpx for HTTP operations with connection pooling and retry logic.

Version: 1.0.0
Created: 2025-12-17

Usage:
    from dakb_client import DAKBClient

    client = DAKBClient(base_url="http://localhost:3100", token="your-token")

    # Search knowledge
    results = client.search("machine learning patterns")

    # Store knowledge
    entry = client.store_knowledge(
        title="PPO Training Tips",
        content="Always normalize your rewards...",
        content_type="lesson_learned",
        category="ml",
    )

    # Send message
    client.send_message(
        recipient_id="backend",
        subject="Task complete",
        content="The migration is finished.",
    )
"""

import json
import logging
from typing import Any, Optional
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
    Knowledge,
    KnowledgeCreate,
    SearchResult,
    SearchResults,
    Message,
    MessageCreate,
    MessageStats,
    MessageStatus,
    Vote,
    VoteResult,
    DAKBStatus,
    DAKBStats,
    ContentType,
    Category,
    AccessLevel,
    MessagePriority,
    VoteType,
)

logger = logging.getLogger(__name__)


class DAKBClient:
    """
    Synchronous DAKB API client.

    Provides methods for all DAKB operations including knowledge management,
    messaging, voting, and session handling.

    Args:
        base_url: DAKB gateway URL (e.g., "http://localhost:3100")
        token: Authentication token
        timeout: Request timeout in seconds (default: 30)
        max_retries: Maximum retry attempts for failed requests (default: 3)
        verify_ssl: Verify SSL certificates (default: True)

    Example:
        client = DAKBClient("http://localhost:3100", "my-token")
        results = client.search("error handling patterns")
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

        # Configure httpx client with connection pooling
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            verify=verify_ssl,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
            ),
        )

    def close(self) -> None:
        """Close the HTTP client and release resources."""
        self._client.close()

    def __enter__(self) -> "DAKBClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # =========================================================================
    # HTTP HELPERS
    # =========================================================================

    def _api_url(self, path: str) -> str:
        """Build full API URL."""
        return f"/api/{self.API_VERSION}/{path.lstrip('/')}"

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """
        Handle HTTP response and raise appropriate exceptions.

        Args:
            response: httpx Response object

        Returns:
            Parsed JSON response data

        Raises:
            DAKBAuthenticationError: For 401 responses
            DAKBNotFoundError: For 404 responses
            DAKBValidationError: For 400/422 responses
            DAKBRateLimitError: For 429 responses
            DAKBServerError: For 5xx responses
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

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Make HTTP request with retry logic.

        Args:
            method: HTTP method
            path: API path
            data: Request body (for POST/PUT/PATCH)
            params: Query parameters

        Returns:
            Response data

        Raises:
            DAKBConnectionError: On connection failure
            DAKBTimeoutError: On request timeout
        """
        url = self._api_url(path)

        for attempt in range(self.max_retries):
            try:
                if method.upper() == "GET":
                    response = self._client.get(url, params=params)
                elif method.upper() == "POST":
                    response = self._client.post(url, json=data, params=params)
                elif method.upper() == "PUT":
                    response = self._client.put(url, json=data, params=params)
                elif method.upper() == "PATCH":
                    response = self._client.patch(url, json=data, params=params)
                elif method.upper() == "DELETE":
                    response = self._client.delete(url, params=params)
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

            except httpx.TimeoutException as e:
                if attempt == self.max_retries - 1:
                    raise DAKBTimeoutError(
                        f"Request timed out: {e}",
                        timeout_seconds=self.timeout,
                        operation=f"{method} {path}",
                    )
                logger.warning(f"Timeout (attempt {attempt + 1}): {e}")

        # If we get here, all retries exhausted without specific exception
        raise DAKBError(
            f"Request failed after {self.max_retries} retries",
            code="RETRY_EXHAUSTED",
            details={"method": method, "path": path, "retries": self.max_retries},
        )

    def __repr__(self) -> str:
        """String representation with masked token for security."""
        masked_token = f"{self.token[:4]}...{self.token[-4:]}" if len(self.token) > 8 else "****"
        return f"DAKBClient(base_url={self.base_url!r}, token={masked_token!r})"

    # =========================================================================
    # KNOWLEDGE OPERATIONS
    # =========================================================================

    def store_knowledge(
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
        """
        Store new knowledge entry.

        Args:
            title: Brief title (max 100 chars)
            content: Knowledge content (markdown supported)
            content_type: Type (lesson_learned, research, report, pattern, config, error_fix)
            category: Category (database, ml, trading, devops, security, frontend, backend, general)
            confidence: Confidence score 0-1 (default: 0.8)
            tags: Searchable tags (max 10)
            related_files: Related file paths
            access_level: Access control (public, restricted, secret)

        Returns:
            Created knowledge entry with ID

        Example:
            result = client.store_knowledge(
                title="Redis caching pattern",
                content="Use Redis for session caching with TTL...",
                content_type="pattern",
                category="backend",
                tags=["redis", "caching"],
            )
        """
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
        return self._request("POST", "knowledge", data=data)

    def search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.3,
        category: Optional[str | Category] = None,
    ) -> dict[str, Any]:
        """
        Search knowledge base semantically.

        Args:
            query: Natural language search query
            limit: Maximum results (default: 5, max: 50)
            min_score: Minimum similarity score 0-1 (default: 0.3)
            category: Filter by category (optional)

        Returns:
            Search results with similarity scores

        Example:
            results = client.search("error handling best practices", limit=10)
        """
        params: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "min_score": min_score,
        }
        if category:
            params["category"] = category if isinstance(category, str) else category.value

        return self._request("GET", "knowledge/search", params=params)

    def get_knowledge(self, knowledge_id: str) -> dict[str, Any]:
        """
        Get full knowledge entry by ID.

        Args:
            knowledge_id: Knowledge entry ID (e.g., "kn_20251217_abc123")

        Returns:
            Full knowledge entry

        Raises:
            DAKBNotFoundError: If knowledge ID doesn't exist
        """
        return self._request("GET", f"knowledge/{knowledge_id}")

    def vote(
        self,
        knowledge_id: str,
        vote: str | VoteType,
        comment: Optional[str] = None,
        used_successfully: Optional[bool] = None,
    ) -> dict[str, Any]:
        """
        Vote on knowledge quality.

        Args:
            knowledge_id: Knowledge entry to vote on
            vote: Vote type (helpful, unhelpful, outdated, incorrect)
            comment: Optional comment (max 500 chars)
            used_successfully: Whether knowledge was used successfully

        Returns:
            Vote result with updated counts
        """
        data: dict[str, Any] = {
            "knowledge_id": knowledge_id,
            "vote": vote if isinstance(vote, str) else vote.value,
        }
        if comment:
            data["comment"] = comment
        if used_successfully is not None:
            data["used_successfully"] = used_successfully

        return self._request("POST", "knowledge/vote", data=data)

    # =========================================================================
    # MESSAGING OPERATIONS
    # =========================================================================

    def send_message(
        self,
        recipient_id: str,
        subject: str,
        content: str,
        priority: str | MessagePriority = "normal",
        thread_id: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        expires_in_hours: int = 168,
    ) -> dict[str, Any]:
        """
        Send direct message to another agent.

        Args:
            recipient_id: Target agent ID or alias
            subject: Message subject (max 200 chars)
            content: Message body (markdown supported)
            priority: Priority level (low, normal, high, urgent)
            thread_id: Thread ID for replies
            reply_to_id: Message ID being replied to
            expires_in_hours: Hours until expiration (default: 168 = 7 days)

        Returns:
            Sent message with ID

        Example:
            result = client.send_message(
                recipient_id="backend",
                subject="Task assignment",
                content="Please implement the new API endpoint.",
                priority="high",
            )
        """
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

        return self._request("POST", "messages", data=data)

    def get_messages(
        self,
        status: Optional[str | MessageStatus] = None,
        priority: Optional[str | MessagePriority] = None,
        sender_id: Optional[str] = None,
        include_broadcasts: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """
        Get messages from inbox.

        Args:
            status: Filter by status (pending, delivered, read, expired)
            priority: Filter by priority (low, normal, high, urgent)
            sender_id: Filter by sender
            include_broadcasts: Include broadcast messages (default: True)
            page: Page number (default: 1)
            page_size: Items per page (default: 20, max: 100)

        Returns:
            Paginated message list
        """
        params: dict[str, Any] = {
            "include_broadcasts": include_broadcasts,
            "page": page,
            "page_size": page_size,
        }
        if status:
            params["status"] = status if isinstance(status, str) else status.value
        if priority:
            params["priority"] = priority if isinstance(priority, str) else priority.value
        if sender_id:
            params["sender_id"] = sender_id

        return self._request("GET", "messages", params=params)

    def mark_read(
        self,
        message_id: Optional[str] = None,
        message_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Mark message(s) as read.

        Args:
            message_id: Single message ID to mark as read
            message_ids: Multiple message IDs (batch operation)

        Returns:
            Operation result
        """
        data: dict[str, Any] = {}
        if message_id:
            data["message_id"] = message_id
        if message_ids:
            data["message_ids"] = message_ids

        return self._request("POST", "messages/mark-read", data=data)

    def broadcast(
        self,
        subject: str,
        content: str,
        priority: str | MessagePriority = "normal",
        expires_in_hours: int = 168,
    ) -> dict[str, Any]:
        """
        Send broadcast message to all agents.

        Args:
            subject: Broadcast subject (max 200 chars)
            content: Broadcast body
            priority: Priority level (default: normal)
            expires_in_hours: Hours until expiration (default: 168)

        Returns:
            Broadcast result
        """
        data = {
            "subject": subject,
            "content": content,
            "priority": priority if isinstance(priority, str) else priority.value,
            "expires_in_hours": expires_in_hours,
        }
        return self._request("POST", "messages/broadcast", data=data)

    def get_message_stats(self) -> dict[str, Any]:
        """
        Get messaging statistics for current agent.

        Returns:
            Message statistics including counts by priority and status
        """
        return self._request("GET", "messages/stats")

    # =========================================================================
    # STATUS AND STATS
    # =========================================================================

    def status(self) -> dict[str, Any]:
        """
        Check DAKB service health status.

        Returns:
            Service health information
        """
        return self._request("GET", "status")

    def get_stats(self) -> dict[str, Any]:
        """
        Get knowledge base statistics.

        Returns:
            Statistics including totals by category and content type
        """
        return self._request("GET", "stats")

    # =========================================================================
    # ADVANCED OPERATIONS
    # =========================================================================

    def advanced(
        self,
        operation: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Execute advanced DAKB operation via proxy.

        Available operations:
        - Bulk: bulk_store, list_by_category, list_by_tags
        - Discovery: find_related, cleanup_expired
        - Reputation: get_vote_summary, get_agent_reputation, get_leaderboard
        - Sessions: session_start, session_status, session_end, session_export
        - Registration: create_invite, register_with_invite, revoke_agent
        - Aliases: register_alias, list_aliases, deactivate_alias, resolve_alias

        Args:
            operation: Operation name
            params: Operation parameters

        Returns:
            Operation result

        Example:
            # Get leaderboard
            result = client.advanced("get_leaderboard", {"limit": 10})

            # Register alias
            result = client.advanced("register_alias", {"alias": "MyBot"})
        """
        data = {
            "operation": operation,
            "params": params or {},
        }
        return self._request("POST", "advanced", data=data)

    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================

    def ping(self) -> bool:
        """
        Quick health check.

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            status = self.status()
            return status.get("success", False) or status.get("gateway_status") == "ok"
        except DAKBError:
            return False

    def store_lesson_learned(
        self,
        title: str,
        content: str,
        category: str | Category,
        tags: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Convenience method to store a lesson learned.

        Args:
            title: Lesson title
            content: Lesson content
            category: Knowledge category
            tags: Optional tags

        Returns:
            Created knowledge entry
        """
        return self.store_knowledge(
            title=title,
            content=content,
            content_type=ContentType.LESSON_LEARNED,
            category=category,
            tags=tags,
        )

    def store_error_fix(
        self,
        title: str,
        content: str,
        category: str | Category,
        related_files: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Convenience method to store an error fix.

        Args:
            title: Error description
            content: Fix description
            category: Knowledge category
            related_files: Files involved in the fix

        Returns:
            Created knowledge entry
        """
        return self.store_knowledge(
            title=title,
            content=content,
            content_type=ContentType.ERROR_FIX,
            category=category,
            related_files=related_files,
        )
