"""
DAKB MCP Tool Handlers

Handler implementations for DAKB MCP tools. These handlers process
tool calls and interact with the DAKB Gateway REST API.

Version: 1.9
Created: 2025-12-08
Updated: 2025-12-17
Author: Backend Agent (Claude Opus 4.5)

Handlers (Basic CRUD - Step 2.1):
- handle_store_knowledge: Store new knowledge
- handle_search: Semantic search
- handle_get_knowledge: Get knowledge by ID
- handle_vote: Vote on knowledge
- handle_status: Get system status

Handlers (Knowledge Management - Step 2.2):
- handle_bulk_store: Store multiple entries
- handle_list_by_category: List by category with pagination
- handle_list_by_tags: List by tags
- handle_find_related: Find semantically related entries
- handle_get_stats: Get detailed statistics
- handle_cleanup_expired: Cleanup expired entries (admin)

Handlers (Voting & Reputation - Step 2.3):
- handle_get_vote_summary: Get detailed vote summary
- handle_get_agent_reputation: Get agent reputation metrics
- handle_get_leaderboard: Get agent leaderboard
- handle_get_my_contributions: Get caller's contributions
- handle_flag_for_review: Flag knowledge for review
- handle_moderate: Moderate flagged knowledge (admin)

Handlers (Messaging - Phase 3):
- handle_send_message: Send a direct message (supports alias recipients)
- handle_get_messages: Get messages (inbox) - shared across token aliases
- handle_mark_read: Mark message(s) as read
- handle_broadcast: Send broadcast message
- handle_get_message_stats: Get message statistics

Handlers (Session Management - Phase 4):
- handle_session_start: Start a new session
- handle_session_status: Get session status
- handle_session_end: End a session
- handle_session_export: Export session for handoff
- handle_session_import: Import session from handoff
- handle_git_context: Capture git context for session

Handlers (Alias Management - Phase 5):
- handle_register_alias: Register alias for current token
- handle_list_aliases: List aliases for current token
- handle_deactivate_alias: Deactivate (soft delete) alias
- handle_resolve_alias: Resolve alias to token_id

Handlers (Profile System - v1.3):
- handle_advanced: Proxy handler for advanced operations
- handle_advanced_help: Help for advanced operations

Changes in 1.9:
- Added notification hints in tool responses (_notifications field)
- New NotificationHints dataclass for passive message awareness
- get_notification_hints() helper checks inbox for pending messages
- dispatch_tool() injects hints into successful responses
- Exempt tools: dakb_get_messages, dakb_get_message_stats, dakb_mark_read, dakb_status
- Configurable via DAKB_NOTIFICATION_HINTS env var (default: true)

Changes in 1.8:
- Fixed session_export/import parameter mismatches with gateway API
- export_session now sends request body instead of query params
- import_session now accepts package_json string (not dict)
- Fixed handle_session_export to return package_json from gateway response
- Updated ADVANCED_OPERATION_PARAMS documentation

Changes in 1.7:
- Added 4 new handlers for Phase 5 alias management tools
- Added gateway client methods for alias operations (register, list, deactivate, resolve)
- Updated messaging handlers documentation to mention alias support

Changes in 1.6:
- Added handle_advanced proxy handler for profile system v1.3
- Added handle_advanced_help for operation parameter discovery
- Added ADVANCED_OPERATION_HANDLERS mapping
- Added ADVANCED_OPERATION_PARAMS documentation
- Reduces MCP token usage by ~46% when using standard profile

Changes in 1.5:
- Added 6 new handlers for Phase 4 session management tools
- Added gateway client methods for session operations

Changes in 1.4:
- Added 5 new handlers for Phase 3 messaging tools
- Added gateway client methods for messaging operations

Changes in 1.3:
- Added 6 new handlers for Step 2.3 voting & reputation tools
- Added gateway client methods for reputation, leaderboard, flags, moderation

Changes in 1.2:
- Added 6 new handlers for Step 2.2 knowledge management tools
- Added gateway client methods for bulk, list, related, stats, cleanup

Changes in 1.1:
- ISS-029: Added async lock for thread-safe client initialization
- ISS-030: Added token expiration tracking and validation
- ISS-031: Implemented persistent HTTP client with connection pooling
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class DAKBClientConfig:
    """Configuration for DAKB Gateway client."""
    gateway_url: str = "http://localhost:3100"
    timeout: float = 30.0
    auth_token: str | None = None

    @classmethod
    def from_env(cls) -> "DAKBClientConfig":
        """
        Load configuration from environment variables.

        Environment variables:
        - DAKB_GATEWAY_URL: Gateway URL (default: http://localhost:3100)
        - DAKB_CLIENT_TIMEOUT: Request timeout in seconds (default: 30)
        - DAKB_AUTH_TOKEN: Pre-configured auth token (optional)
        """
        return cls(
            gateway_url=os.getenv("DAKB_GATEWAY_URL", "http://localhost:3100"),
            timeout=float(os.getenv("DAKB_CLIENT_TIMEOUT", "30")),
            auth_token=os.getenv("DAKB_AUTH_TOKEN"),
        )


# =============================================================================
# ERROR TYPES
# =============================================================================

class DAKBError(Exception):
    """Base exception for DAKB operations."""
    def __init__(self, message: str, code: str = "DAKB_ERROR"):
        super().__init__(message)
        self.code = code
        self.message = message


class DAKBConnectionError(DAKBError):
    """Error connecting to DAKB Gateway."""
    def __init__(self, message: str):
        super().__init__(message, "CONNECTION_ERROR")


class DAKBAuthError(DAKBError):
    """Authentication error."""
    def __init__(self, message: str):
        super().__init__(message, "AUTH_ERROR")


class DAKBNotFoundError(DAKBError):
    """Resource not found."""
    def __init__(self, message: str):
        super().__init__(message, "NOT_FOUND")


class DAKBValidationError(DAKBError):
    """Input validation error."""
    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR")


class DAKBTokenExpiredError(DAKBError):
    """Token has expired or is invalid."""
    def __init__(self, message: str = "Authentication token has expired"):
        super().__init__(message, "TOKEN_EXPIRED")


# =============================================================================
# RESPONSE MODELS
# =============================================================================

@dataclass
class NotificationHints:
    """
    Notification summary included in tool responses.

    Provides passive awareness of pending messages without explicit polling.
    """
    unread: int = 0
    urgent: int = 0
    high: int = 0
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "unread": self.unread,
            "urgent": self.urgent,
            "high": self.high,
        }
        if self.hint:
            result["hint"] = self.hint
        return result


@dataclass
class ToolResponse:
    """
    Standardized response from tool handlers.

    Attributes:
        success: Whether the operation succeeded.
        data: Response data (varies by tool).
        error: Error message if success is False.
        error_code: Error code for programmatic handling.
        notifications: Notification hints (unread count, urgent messages).
    """
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    notifications: NotificationHints | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for MCP response."""
        result: dict[str, Any] = {"success": self.success}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error
        if self.error_code is not None:
            result["error_code"] = self.error_code
        if self.notifications is not None:
            result["_notifications"] = self.notifications.to_dict()
        return result

    def to_mcp_content(self) -> list[dict[str, Any]]:
        """Convert to MCP content format."""
        import json
        return [
            {
                "type": "text",
                "text": json.dumps(self.to_dict(), indent=2),
            }
        ]


# =============================================================================
# GATEWAY CLIENT
# =============================================================================

class DAKBGatewayClient:
    """
    Async HTTP client for DAKB Gateway API.

    Handles authentication, request formatting, and error handling
    for all DAKB Gateway operations.

    ISS-031: Uses a persistent HTTP client with connection pooling for efficiency.
    ISS-030: Tracks token expiration and validates before requests.
    """

    # Default token lifetime in seconds (1 hour)
    # Can be overridden when setting the token
    DEFAULT_TOKEN_LIFETIME: int = 3600

    def __init__(self, config: DAKBClientConfig | None = None):
        """
        Initialize gateway client.

        Args:
            config: Client configuration. If None, loads from environment.
        """
        self.config = config or DAKBClientConfig.from_env()
        self._token: str | None = self.config.auth_token
        # ISS-030: Track when token was set for expiration checking
        self._token_set_at: float | None = time.time() if self._token else None
        self._token_lifetime: int = self.DEFAULT_TOKEN_LIFETIME
        # ISS-031: Persistent HTTP client (lazy initialization)
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """
        Get or create the persistent HTTP client.

        ISS-031: Reuses the same client across requests for connection pooling.

        Returns:
            The persistent httpx.AsyncClient instance.
        """
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=self.config.timeout,
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                    keepalive_expiry=30.0,
                ),
            )
        return self._http_client

    async def close(self) -> None:
        """
        Close the HTTP client and release resources.

        ISS-031: Should be called when the client is no longer needed.
        """
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
            logger.debug("DAKB Gateway HTTP client closed")

    def set_token(self, token: str, lifetime_seconds: int | None = None) -> None:
        """
        Set the authentication token.

        ISS-030: Tracks token set time for expiration checking.

        Args:
            token: JWT/HMAC token for authentication.
            lifetime_seconds: Token lifetime in seconds (default: 3600).
        """
        self._token = token
        self._token_set_at = time.time()
        if lifetime_seconds is not None:
            self._token_lifetime = lifetime_seconds
        logger.debug(f"Token set with lifetime of {self._token_lifetime} seconds")

    def is_token_valid(self) -> bool:
        """
        Check if the current token appears valid (not expired).

        ISS-030: Validates token based on set time and lifetime.

        Returns:
            True if token is set and not expired, False otherwise.
        """
        if self._token is None or self._token_set_at is None:
            return False

        elapsed = time.time() - self._token_set_at
        is_valid = elapsed < self._token_lifetime

        if not is_valid:
            logger.warning(
                f"Token may be expired: set {elapsed:.0f}s ago, "
                f"lifetime is {self._token_lifetime}s"
            )

        return is_valid

    def get_token_age_seconds(self) -> float | None:
        """
        Get the age of the current token in seconds.

        Returns:
            Age in seconds, or None if no token is set.
        """
        if self._token_set_at is None:
            return None
        return time.time() - self._token_set_at

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _check_token_before_request(self) -> None:
        """
        Check token validity before making a request.

        ISS-030: Logs warning if token may be expired but still attempts request.
        Raises exception only if no token is set for authenticated endpoints.
        """
        if self._token is not None and not self.is_token_valid():
            # Token exists but may be expired - log warning but continue
            # The server will reject if truly expired
            logger.warning(
                "Token may be expired. Consider refreshing authentication. "
                f"Token age: {self.get_token_age_seconds():.0f}s"
            )

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Make HTTP request to gateway.

        ISS-030: Checks token validity before request.
        ISS-031: Uses persistent HTTP client.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            endpoint: API endpoint (e.g., "/api/v1/knowledge").
            data: Request body data.
            params: Query parameters.
            timeout: Request timeout in seconds (defaults to config.timeout).

        Returns:
            Response JSON data.

        Raises:
            DAKBConnectionError: If gateway is unreachable.
            DAKBAuthError: If authentication fails.
            DAKBTokenExpiredError: If token is expired.
            DAKBNotFoundError: If resource not found.
            DAKBValidationError: If request validation fails.
            DAKBError: For other errors.
        """
        # ISS-030: Check token validity before request
        self._check_token_before_request()

        url = f"{self.config.gateway_url}{endpoint}"
        headers = self._get_headers()

        # ISS-031: Use persistent HTTP client instead of creating new one
        client = await self._get_http_client()
        try:
            # Use provided timeout or fall back to config default
            request_timeout = timeout if timeout is not None else self.config.timeout
            response = await client.request(
                method=method,
                url=url,
                json=data,
                params=params,
                headers=headers,
                timeout=request_timeout,
            )

            # Handle HTTP errors
            if response.status_code == 401:
                # ISS-030: Distinguish between missing auth and expired token
                if self._token is not None:
                    # Had a token but it was rejected - likely expired
                    raise DAKBTokenExpiredError(
                        "Authentication token rejected - may be expired"
                    )
                else:
                    raise DAKBAuthError("Authentication required")
            elif response.status_code == 403:
                raise DAKBAuthError("Access denied")
            elif response.status_code == 404:
                raise DAKBNotFoundError("Resource not found")
            elif response.status_code == 422:
                error_detail = response.json().get("detail", "Validation error")
                raise DAKBValidationError(f"Validation error: {error_detail}")
            elif response.status_code == 429:
                raise DAKBError("Rate limit exceeded", "RATE_LIMITED")
            elif response.status_code >= 500:
                raise DAKBError(
                    f"Gateway server error: {response.status_code}",
                    "SERVER_ERROR"
                )

            response.raise_for_status()

            # Handle empty responses (204 No Content)
            if response.status_code == 204:
                return {}

            return response.json()

        except httpx.TimeoutException:
            logger.error(f"Gateway timeout: {url}")
            raise DAKBConnectionError("Gateway request timed out")
        except httpx.ConnectError:
            logger.error(f"Cannot connect to gateway: {url}")
            raise DAKBConnectionError(
                f"Cannot connect to DAKB Gateway at {self.config.gateway_url}"
            )
        except httpx.RequestError as e:
            logger.error(f"Gateway request error: {e}")
            raise DAKBConnectionError(f"Gateway request failed: {str(e)}")

    # -------------------------------------------------------------------------
    # API Methods
    # -------------------------------------------------------------------------

    async def store_knowledge(
        self,
        title: str,
        content: str,
        content_type: str,
        category: str,
        tags: list[str] | None = None,
        access_level: str = "public",
        related_files: list[str] | None = None,
        confidence: float = 0.8,
    ) -> dict[str, Any]:
        """
        Store new knowledge in DAKB.

        Args:
            title: Knowledge title.
            content: Knowledge content.
            content_type: Type of knowledge.
            category: Knowledge category.
            tags: Searchable tags.
            access_level: Access control level.
            related_files: Related file paths.
            confidence: Confidence score.

        Returns:
            Created knowledge entry.
        """
        data = {
            "title": title,
            "content": content,
            "content_type": content_type,
            "category": category,
            "tags": tags or [],
            "access_level": access_level,
            "related_files": related_files or [],
            "confidence": confidence,
        }

        return await self._request("POST", "/api/v1/knowledge", data=data)

    async def search(
        self,
        query: str,
        limit: int = 5,
        category: str | None = None,
        min_score: float | None = None,
    ) -> dict[str, Any]:
        """
        Semantic search across knowledge base.

        Args:
            query: Search query.
            limit: Maximum results.
            category: Category filter.
            min_score: Minimum similarity score.

        Returns:
            Search results.
        """
        params: dict[str, Any] = {"query": query, "k": limit}
        if category:
            params["category"] = category
        if min_score is not None:
            params["min_score"] = min_score

        # Use longer timeout for search (involves embedding generation)
        return await self._request(
            "GET",
            "/api/v1/knowledge/search",
            params=params,
            timeout=60.0  # 1 minute for embedding + vector search
        )

    async def get_knowledge(self, knowledge_id: str) -> dict[str, Any]:
        """
        Get knowledge entry by ID.

        Args:
            knowledge_id: Knowledge identifier.

        Returns:
            Knowledge entry.
        """
        return await self._request("GET", f"/api/v1/knowledge/{knowledge_id}")

    async def vote(
        self,
        knowledge_id: str,
        vote: str,
        comment: str | None = None,
        used_successfully: bool | None = None,
    ) -> dict[str, Any]:
        """
        Vote on knowledge quality.

        Args:
            knowledge_id: Knowledge identifier.
            vote: Vote type.
            comment: Optional comment.
            used_successfully: Whether knowledge was used successfully.

        Returns:
            Updated knowledge entry.
        """
        data: dict[str, Any] = {"vote": vote}
        if comment:
            data["comment"] = comment
        if used_successfully is not None:
            data["used_successfully"] = used_successfully

        return await self._request(
            "POST",
            f"/api/v1/knowledge/{knowledge_id}/vote",
            data=data
        )

    async def get_status(self) -> dict[str, Any]:
        """
        Get DAKB system status.

        Returns:
            System status including service health and statistics.
        """
        return await self._request("GET", "/health")

    # -------------------------------------------------------------------------
    # Knowledge Management Methods (Step 2.2)
    # -------------------------------------------------------------------------

    async def bulk_store(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Store multiple knowledge entries in bulk.

        Args:
            entries: List of knowledge entry dictionaries.

        Returns:
            Bulk operation results with created_ids and failures.
        """
        return await self._request(
            "POST",
            "/api/v1/knowledge/bulk",
            data={"entries": entries}
        )

    async def list_by_category(
        self,
        category: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """
        List knowledge entries by category with pagination.

        Args:
            category: Category to filter by.
            page: Page number (1-indexed).
            page_size: Items per page.

        Returns:
            Paginated list of knowledge entries.
        """
        params = {
            "category": category,
            "page": page,
            "page_size": page_size,
        }
        return await self._request("GET", "/api/v1/knowledge", params=params)

    async def list_by_tags(
        self,
        tags: list[str],
        match_all: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """
        List knowledge entries by tags.

        Args:
            tags: Tags to search for.
            match_all: If True, entries must have all tags.
            limit: Maximum results.

        Returns:
            List of matching knowledge entries.
        """
        params = {
            "tags": ",".join(tags),
            "match_all": str(match_all).lower(),
            "limit": limit,
        }
        return await self._request(
            "GET",
            "/api/v1/knowledge/by-tags",
            params=params
        )

    async def find_related(
        self,
        knowledge_id: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        """
        Find knowledge entries related to a given entry.

        Args:
            knowledge_id: ID of the knowledge entry.
            limit: Maximum related entries.

        Returns:
            List of related knowledge entries with similarity scores.
        """
        params = {"limit": limit}
        return await self._request(
            "GET",
            f"/api/v1/knowledge/{knowledge_id}/related",
            params=params
        )

    async def get_stats(self) -> dict[str, Any]:
        """
        Get detailed knowledge base statistics.

        Returns:
            Statistics including counts by category, content_type, etc.
        """
        return await self._request("GET", "/api/v1/knowledge/stats")

    async def cleanup_expired(
        self,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """
        Cleanup expired knowledge entries.

        Args:
            dry_run: If True, preview without deleting.

        Returns:
            Cleanup results with expired_count and deleted_ids.
        """
        params = {"dry_run": str(dry_run).lower()}
        return await self._request(
            "POST",
            "/api/v1/knowledge/cleanup-expired",
            params=params
        )

    # -------------------------------------------------------------------------
    # Voting & Reputation Methods (Step 2.3)
    # -------------------------------------------------------------------------

    async def get_vote_summary(
        self,
        knowledge_id: str,
    ) -> dict[str, Any]:
        """
        Get detailed vote summary for a knowledge entry.

        Args:
            knowledge_id: Knowledge identifier.

        Returns:
            Vote summary with counts, quality score, and history.
        """
        return await self._request(
            "GET",
            f"/api/v1/knowledge/{knowledge_id}/votes"
        )

    async def get_agent_reputation(
        self,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Get reputation metrics for an agent.

        Args:
            agent_id: Agent identifier. If None, returns caller's reputation.

        Returns:
            Reputation metrics including score, rank, contributions.
        """
        params = {}
        if agent_id:
            params["agent_id"] = agent_id
        return await self._request(
            "GET",
            "/api/v1/reputation",
            params=params if params else None
        )

    async def get_leaderboard(
        self,
        metric: str = "reputation",
        limit: int = 10,
    ) -> dict[str, Any]:
        """
        Get agent leaderboard by metric.

        Args:
            metric: Metric to rank by (reputation, contributions, helpfulness).
            limit: Maximum entries to return.

        Returns:
            Leaderboard entries with rank, agent_id, score.
        """
        params = {
            "metric": metric,
            "limit": limit,
        }
        return await self._request(
            "GET",
            "/api/v1/reputation/leaderboard",
            params=params
        )

    async def get_my_contributions(self) -> dict[str, Any]:
        """
        Get caller's contributions summary.

        Returns:
            Contributions including knowledge entries, votes, reputation history.
        """
        return await self._request(
            "GET",
            "/api/v1/reputation/contributions"
        )

    async def flag_for_review(
        self,
        knowledge_id: str,
        reason: str,
        details: str | None = None,
    ) -> dict[str, Any]:
        """
        Flag knowledge for moderation review.

        Args:
            knowledge_id: Knowledge to flag.
            reason: Reason for flagging (outdated, incorrect, duplicate, spam).
            details: Additional details.

        Returns:
            Flag creation result with flag_id.
        """
        data: dict[str, Any] = {
            "knowledge_id": knowledge_id,
            "reason": reason,
        }
        if details:
            data["details"] = details

        return await self._request(
            "POST",
            "/api/v1/moderation/flag",
            data=data
        )

    async def moderate(
        self,
        knowledge_id: str,
        action: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        Take moderation action on knowledge.

        Args:
            knowledge_id: Knowledge to moderate.
            action: Action to take (approve, deprecate, delete).
            reason: Reason for action.

        Returns:
            Moderation result with new status.
        """
        data: dict[str, Any] = {
            "knowledge_id": knowledge_id,
            "action": action,
        }
        if reason:
            data["reason"] = reason

        return await self._request(
            "POST",
            "/api/v1/moderation/action",
            data=data
        )

    # -------------------------------------------------------------------------
    # Messaging Methods (Phase 3)
    # -------------------------------------------------------------------------

    async def send_message(
        self,
        recipient_id: str,
        subject: str,
        content: str,
        priority: str = "normal",
        thread_id: str | None = None,
        reply_to_id: str | None = None,
        expires_in_hours: int = 168,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send a direct message to another agent.

        Args:
            recipient_id: Target agent ID.
            subject: Message subject line.
            content: Message body content.
            priority: Message priority (low, normal, high, urgent).
            thread_id: Optional thread to add message to.
            reply_to_id: Optional message being replied to.
            expires_in_hours: Hours until expiration.
            metadata: Additional metadata.

        Returns:
            Created message with message_id.
        """
        data: dict[str, Any] = {
            "recipient_id": recipient_id,
            "message_type": "direct",
            "subject": subject,
            "content": content,
            "priority": priority,
            "expires_in_hours": expires_in_hours,
            "metadata": metadata or {},
        }

        if thread_id:
            data["thread_id"] = thread_id
        if reply_to_id:
            data["reply_to_id"] = reply_to_id
            data["message_type"] = "reply"

        return await self._request("POST", "/api/v1/messages", data=data)

    async def get_messages(
        self,
        status: str | None = None,
        priority: str | None = None,
        sender_id: str | None = None,
        include_broadcasts: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """
        Get messages for the current agent's inbox.

        Args:
            status: Filter by status (pending, delivered, read, expired).
            priority: Filter by priority (low, normal, high, urgent).
            sender_id: Filter by sender agent ID.
            include_broadcasts: Include broadcast messages.
            page: Page number.
            page_size: Items per page.

        Returns:
            Paginated list of messages.
        """
        params: dict[str, Any] = {
            "include_broadcasts": str(include_broadcasts).lower(),
            "page": page,
            "page_size": page_size,
        }

        if status:
            params["status"] = status
        if priority:
            params["priority"] = priority
        if sender_id:
            params["sender_id"] = sender_id

        return await self._request("GET", "/api/v1/messages", params=params)

    async def mark_read(
        self,
        message_id: str | None = None,
        message_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Mark one or more messages as read.

        Args:
            message_id: Single message ID to mark.
            message_ids: Multiple message IDs for batch operation.

        Returns:
            Result with marked count.
        """
        if message_id:
            return await self._request(
                "POST",
                f"/api/v1/messages/{message_id}/read"
            )
        elif message_ids:
            return await self._request(
                "POST",
                "/api/v1/messages/mark-read-batch",
                data={"message_ids": message_ids}
            )
        else:
            raise DAKBValidationError("Either message_id or message_ids required")

    async def broadcast(
        self,
        subject: str,
        content: str,
        priority: str = "normal",
        expires_in_hours: int = 168,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send a broadcast message to all agents.

        Args:
            subject: Broadcast subject line.
            content: Broadcast content.
            priority: Broadcast priority.
            expires_in_hours: Hours until expiration.
            metadata: Additional metadata.

        Returns:
            Broadcast result with message_id and recipients_count.
        """
        data = {
            "subject": subject,
            "content": content,
            "priority": priority,
            "expires_in_hours": expires_in_hours,
            "metadata": metadata or {},
        }

        return await self._request("POST", "/api/v1/messages/broadcast", data=data)

    async def get_message_stats(self) -> dict[str, Any]:
        """
        Get message statistics for the current agent.

        Returns:
            Message statistics including sent, received, unread counts.
        """
        return await self._request("GET", "/api/v1/messages/stats")

    # -------------------------------------------------------------------------
    # Session Management Methods (Phase 4)
    # -------------------------------------------------------------------------

    async def start_session(
        self,
        project_path: str,
        task_description: str,
        objectives: list[str] | None = None,
        auto_timeout_minutes: int = 30,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Start a new session.

        Args:
            project_path: Path to the project directory.
            task_description: Description of the task being worked on.
            objectives: List of session objectives.
            auto_timeout_minutes: Minutes of inactivity before auto-pause.
            metadata: Additional session metadata.

        Returns:
            Created session with session_id.
        """
        data = {
            "working_directory": project_path,  # API expects working_directory
            "task_description": task_description,
            "timeout_minutes": auto_timeout_minutes,  # API expects timeout_minutes
            "loaded_contexts": [],
            "working_files": [],
        }
        # Add metadata fields if provided
        if metadata:
            data["metadata"] = metadata

        return await self._request("POST", "/api/v1/sessions", data=data)

    async def get_session(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        """
        Get session details by ID.

        Args:
            session_id: Session identifier.

        Returns:
            Session details including status, git context, and metadata.
        """
        return await self._request("GET", f"/api/v1/sessions/{session_id}")

    async def end_session(
        self,
        session_id: str,
        summary: str | None = None,
        files_modified: list[str] | None = None,
        capture_final_context: bool = True,
    ) -> dict[str, Any]:
        """
        End a session.

        Args:
            session_id: Session identifier.
            summary: Optional summary of work completed.
            files_modified: List of files modified during session.
            capture_final_context: Whether to capture final git context.

        Returns:
            Ended session details.
        """
        data: dict[str, Any] = {
            "capture_final_context": capture_final_context,
        }
        if summary:
            data["summary"] = summary
        if files_modified:
            data["files_modified"] = files_modified

        return await self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/end",
            data=data
        )

    async def export_session(
        self,
        session_id: str,
        include_git_context: bool = True,
        include_patch_bundle: bool = True,
        include_stash: bool = False,
        reason: str | None = None,
        notes: str | None = None,
        store_on_server: bool = False,
    ) -> dict[str, Any]:
        """
        Export session for handoff.

        Args:
            session_id: Session identifier.
            include_git_context: Include git context info.
            include_patch_bundle: Include compressed git diff.
            include_stash: Include git stash.
            reason: Reason for export.
            notes: Additional notes.
            store_on_server: Store package on server for remote agent retrieval.
                            Returns handoff_id instead of package_json.

        Returns:
            Handoff package with session data and optional patch bundle.
            If store_on_server=True, returns handoff_id without package_json.
        """
        # Build request body matching ExportSessionRequest model
        data = {
            "include_git_context": include_git_context,
            "include_patch_bundle": include_patch_bundle,
            "include_stash": include_stash,
            "store_on_server": store_on_server,
        }
        if reason:
            data["reason"] = reason
        if notes:
            data["notes"] = notes

        # Use longer timeout for export (involves git operations)
        return await self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/export",
            data=data,
            timeout=120.0  # 2 minutes for git context + patch bundle
        )

    async def import_session(
        self,
        package_json: str | None = None,
        handoff_id: str | None = None,
        target_path: str | None = None,
        apply_patch: bool = False,
    ) -> dict[str, Any]:
        """
        Import session from handoff package.

        Args:
            package_json: JSON string of the handoff package (from export).
                         Not required if handoff_id is provided.
            handoff_id: Handoff ID to fetch package from server (for remote agents).
                       Takes precedence over package_json.
            target_path: Optional target path for the project.
            apply_patch: Whether to apply the patch bundle.

        Returns:
            Imported session with new session_id.
        """
        # Build request body matching ImportSessionRequest model
        data: dict[str, Any] = {
            "apply_patch": apply_patch,
        }
        if handoff_id:
            data["handoff_id"] = handoff_id
        elif package_json:
            data["package_json"] = package_json
        # If neither provided, the gateway will return an error
        if target_path:
            data["target_directory"] = target_path

        return await self._request("POST", "/api/v1/sessions/import", data=data)

    async def capture_git_context(
        self,
        session_id: str,
        include_diff: bool = True,
        include_stash: bool = True,
    ) -> dict[str, Any]:
        """
        Capture current git context for a session.

        Args:
            session_id: Session identifier.
            include_diff: Include uncommitted changes diff.
            include_stash: Include stash list.

        Returns:
            Git context snapshot with branch, commit, changes, etc.
        """
        params = {
            "include_diff": str(include_diff).lower(),
            "include_stash": str(include_stash).lower(),
        }

        return await self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/git-context",
            params=params
        )

    # -------------------------------------------------------------------------
    # Alias Management Methods (Phase 5)
    # -------------------------------------------------------------------------

    async def register_alias(
        self,
        alias: str,
        role: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Register a new alias for the current token.

        Args:
            alias: Unique alias name to register.
            role: Optional role for the alias (e.g., 'orchestration', 'code_review').
            metadata: Optional additional metadata.

        Returns:
            Created alias record with alias_id, token_id, etc.
        """
        data: dict[str, Any] = {
            "alias": alias,
            "metadata": metadata or {},
        }

        if role:
            data["role"] = role

        return await self._request("POST", "/api/v1/aliases", data=data)

    async def list_aliases(
        self,
        active_only: bool = True,
    ) -> dict[str, Any]:
        """
        List aliases registered to the current token.

        Args:
            active_only: If True, only return active aliases.

        Returns:
            List of alias records with total count.
        """
        params = {
            "active_only": str(active_only).lower(),
        }

        return await self._request("GET", "/api/v1/aliases", params=params)

    async def deactivate_alias(
        self,
        alias: str,
    ) -> dict[str, Any]:
        """
        Deactivate (soft delete) an alias.

        Args:
            alias: Alias name to deactivate.

        Returns:
            Deactivation confirmation.
        """
        return await self._request("DELETE", f"/api/v1/aliases/{alias}")

    async def resolve_alias(
        self,
        alias: str,
    ) -> dict[str, Any]:
        """
        Resolve an alias to its owning token_id.

        Args:
            alias: Alias name to resolve.

        Returns:
            Resolution result with token_id and alias.
        """
        return await self._request("GET", f"/api/v1/aliases/resolve/{alias}")


# =============================================================================
# TOOL HANDLERS
# =============================================================================

# ISS-029: Thread-safe global client with async lock
# Using double-check locking pattern for efficiency
_client: DAKBGatewayClient | None = None
_client_lock: asyncio.Lock = asyncio.Lock()


async def get_client() -> DAKBGatewayClient:
    """
    Get or create the global gateway client (thread-safe).

    ISS-029: Uses async lock with double-check pattern for thread-safe
    initialization in concurrent async contexts.

    Returns:
        The global DAKBGatewayClient instance.
    """
    global _client
    # First check without lock (fast path for already initialized)
    if _client is None:
        async with _client_lock:
            # Double-check after acquiring lock (ISS-029)
            if _client is None:
                _client = DAKBGatewayClient()
                logger.debug("DAKB Gateway client initialized")
    return _client


async def set_client_token(token: str, lifetime_seconds: int | None = None) -> None:
    """
    Set authentication token on the global client.

    ISS-030: Supports optional token lifetime for expiration tracking.

    Args:
        token: JWT/HMAC authentication token.
        lifetime_seconds: Token lifetime in seconds (default: 3600).
    """
    client = await get_client()
    client.set_token(token, lifetime_seconds)


async def close_client() -> None:
    """
    Close the global client and release resources.

    ISS-031: Should be called during application shutdown.
    """
    global _client
    if _client is not None:
        await _client.close()
        _client = None
        logger.debug("DAKB Gateway client closed")


async def handle_store_knowledge(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_store_knowledge tool call.

    Args:
        args: Tool arguments (title, content, content_type, category, etc.)

    Returns:
        ToolResponse with created knowledge ID or error.
    """
    try:
        # ISS-029: Use async get_client()
        client = await get_client()

        result = await client.store_knowledge(
            title=args["title"],
            content=args["content"],
            content_type=args["content_type"],
            category=args["category"],
            tags=args.get("tags", []),
            access_level=args.get("access_level", "public"),
            related_files=args.get("related_files", []),
            confidence=args.get("confidence", 0.8),
        )

        return ToolResponse(
            success=True,
            data={
                "knowledge_id": result.get("knowledge_id"),
                "title": result.get("title"),
                "created_at": result.get("created_at"),
                "embedding_indexed": result.get("embedding_indexed", False),
            }
        )

    except DAKBTokenExpiredError as e:
        # ISS-030: Specific handling for token expiration
        logger.warning(f"Token expired during store_knowledge: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Store knowledge error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in store_knowledge: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_search(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_search tool call.

    Args:
        args: Tool arguments (query, limit, category, min_score)

    Returns:
        ToolResponse with search results or error.
    """
    try:
        # ISS-029: Use async get_client()
        client = await get_client()

        result = await client.search(
            query=args["query"],
            limit=args.get("limit", 5),
            category=args.get("category"),
            min_score=args.get("min_score"),
        )

        # Format results for MCP response
        results = result.get("results", [])
        formatted_results = []

        for item in results:
            knowledge = item.get("knowledge", {})
            formatted_results.append({
                "knowledge_id": knowledge.get("knowledge_id"),
                "title": knowledge.get("title"),
                "snippet": (
                    knowledge.get("content", "")[:200] + "..."
                    if len(knowledge.get("content", "")) > 200
                    else knowledge.get("content", "")
                ),
                "score": item.get("similarity_score", 0.0),
                "category": knowledge.get("category"),
                "content_type": knowledge.get("content_type"),
                "votes": knowledge.get("votes", {}),
            })

        return ToolResponse(
            success=True,
            data={
                "results": formatted_results,
                "total": result.get("total", len(formatted_results)),
                "query": result.get("query", args["query"]),
                "search_time_ms": result.get("search_time_ms", 0),
            }
        )

    except DAKBTokenExpiredError as e:
        # ISS-030: Specific handling for token expiration
        logger.warning(f"Token expired during search: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Search error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in search: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_get_knowledge(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_get_knowledge tool call.

    Args:
        args: Tool arguments (knowledge_id)

    Returns:
        ToolResponse with full knowledge entry or error.
    """
    try:
        # ISS-029: Use async get_client()
        client = await get_client()

        knowledge_id = args["knowledge_id"]
        result = await client.get_knowledge(knowledge_id)

        return ToolResponse(
            success=True,
            data={
                "knowledge_id": result.get("knowledge_id"),
                "title": result.get("title"),
                "content": result.get("content"),
                "content_type": result.get("content_type"),
                "category": result.get("category"),
                "tags": result.get("tags", []),
                "access_level": result.get("access_level"),
                "status": result.get("status"),
                "confidence_score": result.get("confidence_score"),
                "votes": result.get("votes", {}),
                "created_at": result.get("created_at"),
                "updated_at": result.get("updated_at"),
                "source": {
                    "agent_id": result.get("source", {}).get("agent_id"),
                    "agent_type": result.get("source", {}).get("agent_type"),
                },
                "related_files": result.get("related_files", []),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Knowledge not found: {args.get('knowledge_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        # ISS-030: Specific handling for token expiration
        logger.warning(f"Token expired during get_knowledge: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Get knowledge error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in get_knowledge: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_vote(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_vote tool call.

    Args:
        args: Tool arguments (knowledge_id, vote, comment, used_successfully)

    Returns:
        ToolResponse with updated vote counts or error.
    """
    try:
        # ISS-029: Use async get_client()
        client = await get_client()

        result = await client.vote(
            knowledge_id=args["knowledge_id"],
            vote=args["vote"],
            comment=args.get("comment"),
            used_successfully=args.get("used_successfully"),
        )

        return ToolResponse(
            success=True,
            data={
                "knowledge_id": result.get("knowledge_id"),
                "votes": result.get("votes", {}),
                "vote_recorded": args["vote"],
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Knowledge not found: {args.get('knowledge_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        # ISS-030: Specific handling for token expiration
        logger.warning(f"Token expired during vote: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Vote error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in vote: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_status(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_status tool call.

    Args:
        args: Tool arguments (empty for this tool)

    Returns:
        ToolResponse with system status or error.
    """
    try:
        # ISS-029: Use async get_client()
        client = await get_client()

        result = await client.get_status()

        # Map gateway health response format
        # Gateway returns: {status, service, version, mongodb, embedding_service}
        return ToolResponse(
            success=True,
            data={
                "gateway_status": result.get("status", "unknown"),
                "embedding_status": result.get("embedding_service", "unknown"),
                "mongodb_status": result.get("mongodb", "unknown"),
                "service": result.get("service", "dakb-gateway"),
                "version": result.get("version", "unknown"),
            }
        )

    except DAKBConnectionError as e:
        # Special handling for connection errors - return degraded status
        return ToolResponse(
            success=True,  # Still "success" but with degraded status
            data={
                "gateway_status": "offline",
                "embedding_status": "unknown",
                "mongodb_status": "unknown",
                "error_detail": e.message,
            }
        )
    except DAKBError as e:
        logger.error(f"Status error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in status: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


# =============================================================================
# KNOWLEDGE MANAGEMENT HANDLERS (Step 2.2)
# =============================================================================

async def handle_bulk_store(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_bulk_store tool call.

    Stores multiple knowledge entries in bulk, handling partial failures.

    Args:
        args: Tool arguments (entries: list of knowledge objects)

    Returns:
        ToolResponse with created_ids, failed entries, and counts.
    """
    try:
        client = await get_client()

        entries = args.get("entries", [])
        if not entries:
            return ToolResponse(
                success=False,
                error="No entries provided",
                error_code="VALIDATION_ERROR"
            )

        result = await client.bulk_store(entries)

        return ToolResponse(
            success=True,
            data={
                "created_ids": result.get("created_ids", []),
                "failed": result.get("failed", []),
                "success_count": result.get("success_count", 0),
                "fail_count": result.get("fail_count", 0),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during bulk_store: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBValidationError as e:
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Bulk store error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in bulk_store: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_list_by_category(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_list_by_category tool call.

    Lists knowledge entries by category with pagination.

    Args:
        args: Tool arguments (category, page, page_size)

    Returns:
        ToolResponse with paginated items.
    """
    try:
        client = await get_client()

        category = args["category"]
        page = args.get("page", 1)
        page_size = args.get("page_size", 20)

        result = await client.list_by_category(category, page, page_size)

        # Format items for MCP response (summary view)
        items = result.get("items", [])
        formatted_items = []
        for item in items:
            formatted_items.append({
                "knowledge_id": item.get("knowledge_id"),
                "title": item.get("title"),
                "content_type": item.get("content_type"),
                "category": item.get("category"),
                "tags": item.get("tags", []),
                "votes": item.get("votes", {}),
                "created_at": item.get("created_at"),
            })

        return ToolResponse(
            success=True,
            data={
                "items": formatted_items,
                "total": result.get("total", len(formatted_items)),
                "page": result.get("page", page),
                "page_size": result.get("page_size", page_size),
                "has_more": len(formatted_items) == page_size,
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during list_by_category: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"List by category error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in list_by_category: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_list_by_tags(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_list_by_tags tool call.

    Lists knowledge entries that match specified tags.

    Args:
        args: Tool arguments (tags, match_all, limit)

    Returns:
        ToolResponse with matching items.
    """
    try:
        client = await get_client()

        tags = args["tags"]
        match_all = args.get("match_all", False)
        limit = args.get("limit", 50)

        result = await client.list_by_tags(tags, match_all, limit)

        # Format items for MCP response
        items = result.get("items", [])
        formatted_items = []
        for item in items:
            formatted_items.append({
                "knowledge_id": item.get("knowledge_id"),
                "title": item.get("title"),
                "content_type": item.get("content_type"),
                "category": item.get("category"),
                "tags": item.get("tags", []),
                "votes": item.get("votes", {}),
                "created_at": item.get("created_at"),
            })

        return ToolResponse(
            success=True,
            data={
                "items": formatted_items,
                "total": result.get("total", len(formatted_items)),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during list_by_tags: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"List by tags error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in list_by_tags: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_find_related(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_find_related tool call.

    Finds knowledge entries semantically related to a given entry.

    Args:
        args: Tool arguments (knowledge_id, limit)

    Returns:
        ToolResponse with related entries and similarity scores.
    """
    try:
        client = await get_client()

        knowledge_id = args["knowledge_id"]
        limit = args.get("limit", 5)

        result = await client.find_related(knowledge_id, limit)

        # Format related items for MCP response
        related = result.get("related", [])
        formatted_related = []
        for item in related:
            knowledge = item.get("knowledge", {})
            formatted_related.append({
                "knowledge_id": knowledge.get("knowledge_id"),
                "title": knowledge.get("title"),
                "snippet": (
                    knowledge.get("content", "")[:200] + "..."
                    if len(knowledge.get("content", "")) > 200
                    else knowledge.get("content", "")
                ),
                "similarity_score": item.get("similarity_score", 0.0),
                "category": knowledge.get("category"),
                "content_type": knowledge.get("content_type"),
            })

        return ToolResponse(
            success=True,
            data={
                "source_id": knowledge_id,
                "related": formatted_related,
                "total": len(formatted_related),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Knowledge not found: {args.get('knowledge_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during find_related: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Find related error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in find_related: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_get_stats(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_get_stats tool call.

    Gets detailed knowledge base statistics.

    Args:
        args: Tool arguments (empty for this tool)

    Returns:
        ToolResponse with statistics breakdown.
    """
    try:
        client = await get_client()

        result = await client.get_stats()

        return ToolResponse(
            success=True,
            data={
                "total_entries": result.get("total_entries", 0),
                "by_category": result.get("by_category", {}),
                "by_content_type": result.get("by_content_type", {}),
                "by_access_level": result.get("by_access_level", {}),
                "top_tags": result.get("top_tags", []),
                "indexed_count": result.get("indexed_count", 0),
                "expired_count": result.get("expired_count", 0),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during get_stats: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Get stats error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in get_stats: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_cleanup_expired(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_cleanup_expired tool call.

    Cleans up expired knowledge entries. Admin-only operation.

    Args:
        args: Tool arguments (dry_run: bool)

    Returns:
        ToolResponse with cleanup results.
    """
    try:
        client = await get_client()

        dry_run = args.get("dry_run", True)

        result = await client.cleanup_expired(dry_run)

        data = {
            "expired_count": result.get("expired_count", 0),
            "dry_run": dry_run,
        }

        # Only include deleted_ids if not a dry run
        if not dry_run:
            data["deleted_ids"] = result.get("deleted_ids", [])
        else:
            # In dry run mode, show preview of what would be deleted
            data["preview_ids"] = result.get("expired_ids", [])

        return ToolResponse(
            success=True,
            data=data
        )

    except DAKBAuthError as e:
        # Cleanup requires admin role
        logger.warning(f"Unauthorized cleanup attempt: {e.message}")
        return ToolResponse(
            success=False,
            error="Admin role required for cleanup operations",
            error_code="UNAUTHORIZED"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during cleanup_expired: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Cleanup expired error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in cleanup_expired: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


# =============================================================================
# VOTING & REPUTATION HANDLERS (Step 2.3)
# =============================================================================

async def handle_get_vote_summary(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_get_vote_summary tool call.

    Gets detailed vote summary for a knowledge entry.

    Args:
        args: Tool arguments (knowledge_id)

    Returns:
        ToolResponse with vote counts, quality score, and history.
    """
    try:
        client = await get_client()

        knowledge_id = args["knowledge_id"]
        result = await client.get_vote_summary(knowledge_id)

        return ToolResponse(
            success=True,
            data={
                "knowledge_id": result.get("knowledge_id", knowledge_id),
                "helpful": result.get("helpful", 0),
                "unhelpful": result.get("unhelpful", 0),
                "outdated": result.get("outdated", 0),
                "incorrect": result.get("incorrect", 0),
                "quality_score": result.get("quality_score", 0.0),
                "total_votes": result.get("total_votes", 0),
                "vote_history": result.get("vote_history", []),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Knowledge not found: {args.get('knowledge_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during get_vote_summary: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Get vote summary error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in get_vote_summary: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_get_agent_reputation(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_get_agent_reputation tool call.

    Gets reputation metrics for an agent.

    Args:
        args: Tool arguments (agent_id - optional)

    Returns:
        ToolResponse with reputation score, rank, and contribution metrics.
    """
    try:
        client = await get_client()

        agent_id = args.get("agent_id")  # Optional - None returns caller's reputation
        result = await client.get_agent_reputation(agent_id)

        return ToolResponse(
            success=True,
            data={
                "agent_id": result.get("agent_id"),
                "reputation_score": result.get("reputation_score", 0.0),
                "rank": result.get("rank"),
                "knowledge_count": result.get("knowledge_contributed", 0),
                "votes_cast": result.get("votes_cast", 0),
                "helpful_votes_received": result.get("helpful_votes_received", 0),
                "unhelpful_votes_received": result.get("unhelpful_votes_received", 0),
                "accuracy_rate": result.get("accuracy_rate", 1.0),
                "helpfulness_rate": result.get("helpfulness_rate", 0.0),
                "vote_weight": result.get("vote_weight", 1.0),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Agent not found: {args.get('agent_id', 'self')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during get_agent_reputation: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Get agent reputation error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in get_agent_reputation: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_get_leaderboard(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_get_leaderboard tool call.

    Gets agent leaderboard by metric.

    Args:
        args: Tool arguments (metric, limit)

    Returns:
        ToolResponse with leaderboard entries.
    """
    try:
        client = await get_client()

        metric = args.get("metric", "reputation")
        limit = args.get("limit", 10)

        result = await client.get_leaderboard(metric, limit)

        # Format leaderboard entries
        entries = result.get("entries", [])
        formatted_entries = []
        for entry in entries:
            formatted_entries.append({
                "rank": entry.get("rank"),
                "agent_id": entry.get("agent_id"),
                "score": entry.get("score", 0),
            })

        return ToolResponse(
            success=True,
            data={
                "metric": metric,
                "entries": formatted_entries,
                "total_agents": result.get("total_agents", len(formatted_entries)),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during get_leaderboard: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Get leaderboard error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in get_leaderboard: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_get_my_contributions(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_get_my_contributions tool call.

    Gets caller's contributions summary.

    Args:
        args: Tool arguments (empty for this tool)

    Returns:
        ToolResponse with knowledge entries, votes, and reputation history.
    """
    try:
        client = await get_client()

        result = await client.get_my_contributions()

        # Format knowledge entries (summary view)
        knowledge_entries = result.get("knowledge_entries", [])
        formatted_knowledge = []
        for entry in knowledge_entries:
            formatted_knowledge.append({
                "knowledge_id": entry.get("knowledge_id"),
                "title": entry.get("title"),
                "category": entry.get("category"),
                "votes": entry.get("votes", {}),
                "created_at": entry.get("created_at"),
            })

        # Format votes cast
        votes_cast = result.get("votes_cast", [])
        formatted_votes = []
        for vote in votes_cast:
            formatted_votes.append({
                "knowledge_id": vote.get("knowledge_id"),
                "vote": vote.get("vote"),
                "voted_at": vote.get("voted_at"),
            })

        return ToolResponse(
            success=True,
            data={
                "agent_id": result.get("agent_id"),
                "knowledge_entries": formatted_knowledge,
                "votes_cast": formatted_votes,
                "reputation_history": result.get("reputation_history", []),
                "reputation_score": result.get("reputation_score", 0.0),
                "rank": result.get("rank"),
                "total_knowledge": result.get("total_knowledge", len(formatted_knowledge)),
                "total_votes": result.get("total_votes", len(formatted_votes)),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during get_my_contributions: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Get my contributions error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in get_my_contributions: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_flag_for_review(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_flag_for_review tool call.

    Flags knowledge for moderation review.

    Args:
        args: Tool arguments (knowledge_id, reason, details)

    Returns:
        ToolResponse with flag creation result.
    """
    try:
        client = await get_client()

        knowledge_id = args["knowledge_id"]
        reason = args["reason"]
        details = args.get("details")

        result = await client.flag_for_review(knowledge_id, reason, details)

        return ToolResponse(
            success=True,
            data={
                "flagged": True,
                "flag_id": result.get("flag_id"),
                "knowledge_id": knowledge_id,
                "reason": reason,
                "status": "pending",
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Knowledge not found: {args.get('knowledge_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during flag_for_review: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBValidationError as e:
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Flag for review error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in flag_for_review: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_moderate(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_moderate tool call.

    Takes moderation action on knowledge. Admin-only operation.

    Args:
        args: Tool arguments (knowledge_id, action, reason)

    Returns:
        ToolResponse with moderation result.
    """
    try:
        client = await get_client()

        knowledge_id = args["knowledge_id"]
        action = args["action"]
        reason = args.get("reason")

        # Validate reason is provided for deprecate/delete
        if action in ["deprecate", "delete"] and not reason:
            return ToolResponse(
                success=False,
                error=f"Reason is required for {action} action",
                error_code="VALIDATION_ERROR"
            )

        result = await client.moderate(knowledge_id, action, reason)

        return ToolResponse(
            success=True,
            data={
                "success": True,
                "knowledge_id": knowledge_id,
                "action": action,
                "new_status": result.get("new_status"),
                "moderated_at": result.get("moderated_at"),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Knowledge not found: {args.get('knowledge_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBAuthError as e:
        # Moderation requires admin role
        logger.warning(f"Unauthorized moderation attempt: {e.message}")
        return ToolResponse(
            success=False,
            error="Admin role required for moderation operations",
            error_code="UNAUTHORIZED"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during moderate: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Moderate error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in moderate: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


# =============================================================================
# MESSAGING HANDLERS (Phase 3)
# =============================================================================

async def handle_send_message(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_send_message tool call.

    Sends a direct message to another agent.

    Args:
        args: Tool arguments (recipient_id, subject, content, priority, etc.)

    Returns:
        ToolResponse with created message_id or error.
    """
    try:
        client = await get_client()

        result = await client.send_message(
            recipient_id=args["recipient_id"],
            subject=args["subject"],
            content=args["content"],
            priority=args.get("priority", "normal"),
            thread_id=args.get("thread_id"),
            reply_to_id=args.get("reply_to_id"),
            expires_in_hours=args.get("expires_in_hours", 168),
            metadata=args.get("metadata"),
        )

        message = result.get("message", {})
        return ToolResponse(
            success=True,
            data={
                "message_id": message.get("message_id"),
                "recipient_id": message.get("recipient_id"),
                "subject": message.get("subject"),
                "priority": message.get("priority"),
                "thread_id": message.get("thread_id"),
                "status": message.get("status", "pending"),
                "created_at": message.get("created_at"),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during send_message: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBValidationError as e:
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Send message error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in send_message: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_get_messages(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_get_messages tool call.

    Gets messages for the current agent's inbox.

    Args:
        args: Tool arguments (status, priority, sender_id, etc.)

    Returns:
        ToolResponse with list of messages or error.
    """
    try:
        client = await get_client()

        result = await client.get_messages(
            status=args.get("status"),
            priority=args.get("priority"),
            sender_id=args.get("sender_id"),
            include_broadcasts=args.get("include_broadcasts", True),
            page=args.get("page", 1),
            page_size=args.get("page_size", 20),
        )

        # Format messages for MCP response (summary view)
        messages = result.get("messages", [])
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({
                "message_id": msg.get("message_id"),
                "sender_id": msg.get("sender_id"),
                "subject": msg.get("subject"),
                "preview": (
                    msg.get("content", "")[:100] + "..."
                    if len(msg.get("content", "")) > 100
                    else msg.get("content", "")
                ),
                "priority": msg.get("priority"),
                "status": msg.get("status"),
                "message_type": msg.get("message_type"),
                "created_at": msg.get("created_at"),
                "thread_id": msg.get("thread_id"),
            })

        return ToolResponse(
            success=True,
            data={
                "messages": formatted_messages,
                "total": result.get("total", len(formatted_messages)),
                "page": result.get("page", args.get("page", 1)),
                "page_size": result.get("page_size", args.get("page_size", 20)),
                "has_more": result.get("has_more", False),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during get_messages: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Get messages error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in get_messages: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_mark_read(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_mark_read tool call.

    Marks one or more messages as read.

    Args:
        args: Tool arguments (message_id or message_ids)

    Returns:
        ToolResponse with mark read result or error.
    """
    try:
        client = await get_client()

        message_id = args.get("message_id")
        message_ids = args.get("message_ids")

        if not message_id and not message_ids:
            return ToolResponse(
                success=False,
                error="Either message_id or message_ids required",
                error_code="VALIDATION_ERROR"
            )

        result = await client.mark_read(
            message_id=message_id,
            message_ids=message_ids,
        )

        if message_id:
            # Single message response
            message = result.get("message", {})
            return ToolResponse(
                success=True,
                data={
                    "message_id": message.get("message_id", message_id),
                    "status": message.get("status", "read"),
                    "marked": True,
                }
            )
        else:
            # Batch response
            return ToolResponse(
                success=True,
                data={
                    "marked_count": result.get("marked_count", 0),
                    "requested_count": result.get("requested_count", len(message_ids)),
                    "success": result.get("success", True),
                }
            )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Message not found: {args.get('message_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during mark_read: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Mark read error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in mark_read: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_broadcast(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_broadcast tool call.

    Sends a broadcast message to all agents.

    Args:
        args: Tool arguments (subject, content, priority, etc.)

    Returns:
        ToolResponse with broadcast result or error.
    """
    try:
        client = await get_client()

        result = await client.broadcast(
            subject=args["subject"],
            content=args["content"],
            priority=args.get("priority", "normal"),
            expires_in_hours=args.get("expires_in_hours", 168),
            metadata=args.get("metadata"),
        )

        return ToolResponse(
            success=True,
            data={
                "message_id": result.get("message_id"),
                "recipients_count": result.get("recipients_count", 0),
                "delivered_count": result.get("delivered_count", 0),
                "status": "sent",
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during broadcast: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Broadcast error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in broadcast: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_get_message_stats(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_get_message_stats tool call.

    Gets message statistics for the current agent.

    Args:
        args: Tool arguments (empty for this tool)

    Returns:
        ToolResponse with message statistics or error.
    """
    try:
        client = await get_client()

        result = await client.get_message_stats()

        return ToolResponse(
            success=True,
            data={
                "agent_id": result.get("agent_id"),
                "total_sent": result.get("total_sent", 0),
                "total_received": result.get("total_received", 0),
                "unread_count": result.get("unread_count", 0),
                "pending_count": result.get("pending_count", 0),
                "by_priority": result.get("by_priority", {}),
                "by_type": result.get("by_type", {}),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during get_message_stats: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Get message stats error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in get_message_stats: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


# =============================================================================
# SESSION MANAGEMENT HANDLERS (Phase 4)
# =============================================================================

async def handle_session_start(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_session_start tool call.

    Starts a new session for tracking work on a project.

    Args:
        args: Tool arguments (project_path, task_description, objectives, etc.)

    Returns:
        ToolResponse with created session_id or error.
    """
    try:
        client = await get_client()

        result = await client.start_session(
            project_path=args["project_path"],
            task_description=args["task_description"],
            objectives=args.get("objectives", []),
            auto_timeout_minutes=args.get("auto_timeout_minutes", 30),
            metadata=args.get("metadata"),
        )

        session = result.get("session", {})
        return ToolResponse(
            success=True,
            data={
                "session_id": session.get("session_id"),
                "status": session.get("status", "active"),
                "project_path": session.get("project_path"),
                "task_description": session.get("task_description"),
                "agent_id": session.get("agent_id"),
                "machine_id": session.get("machine_id"),
                "started_at": session.get("started_at"),
                "auto_timeout_minutes": session.get("auto_timeout_minutes", 30),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during session_start: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBValidationError as e:
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Session start error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in session_start: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_session_status(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_session_status tool call.

    Gets the current status of a session.

    Args:
        args: Tool arguments (session_id)

    Returns:
        ToolResponse with session details or error.
    """
    try:
        client = await get_client()

        session_id = args["session_id"]
        result = await client.get_session(session_id)

        session = result.get("session", result)
        git_context = session.get("git_context", {})

        return ToolResponse(
            success=True,
            data={
                "session_id": session.get("session_id", session_id),
                "status": session.get("status"),
                "project_path": session.get("project_path"),
                "task_description": session.get("task_description"),
                "objectives": session.get("objectives", []),
                "agent_id": session.get("agent_id"),
                "machine_id": session.get("machine_id"),
                "started_at": session.get("started_at"),
                "last_activity": session.get("last_activity"),
                "duration_minutes": session.get("duration_minutes"),
                "git_context": {
                    "branch": git_context.get("branch"),
                    "commit_sha": git_context.get("commit_sha"),
                    "has_uncommitted_changes": git_context.get("has_uncommitted_changes", False),
                    "uncommitted_count": len(git_context.get("uncommitted_changes", [])),
                } if git_context else None,
                "files_modified": session.get("files_modified", []),
                "session_chain": session.get("session_chain", []),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Session not found: {args.get('session_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during session_status: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Session status error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in session_status: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_session_end(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_session_end tool call.

    Ends a session and optionally captures final git context.

    Args:
        args: Tool arguments (session_id, summary, files_modified, capture_final_context)

    Returns:
        ToolResponse with ended session details or error.
    """
    try:
        client = await get_client()

        result = await client.end_session(
            session_id=args["session_id"],
            summary=args.get("summary"),
            files_modified=args.get("files_modified"),
            capture_final_context=args.get("capture_final_context", True),
        )

        session = result.get("session", result)
        return ToolResponse(
            success=True,
            data={
                "session_id": session.get("session_id", args["session_id"]),
                "status": session.get("status", "completed"),
                "ended_at": session.get("ended_at"),
                "duration_minutes": session.get("duration_minutes"),
                "summary": session.get("summary"),
                "files_modified": session.get("files_modified", []),
                "final_git_context_captured": session.get("final_git_context_captured", False),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Session not found: {args.get('session_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during session_end: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Session end error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in session_end: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_session_export(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_session_export tool call.

    Exports a session for handoff to another agent/machine.

    Args:
        args: Tool arguments (session_id, include_git_context, include_patch_bundle, etc.)
            - output_file: Optional path to write package_json to file (prevents truncation)
            - store_on_server: Store package on server for remote agent retrieval

    Returns:
        ToolResponse with handoff package or error.
        If output_file is specified, package_json is written to file and path returned.
        If store_on_server=True, returns handoff_id for remote retrieval (no package_json).
    """
    try:
        client = await get_client()

        # Check if store_on_server is requested (for remote agents)
        store_on_server = args.get("store_on_server", False)

        result = await client.export_session(
            session_id=args["session_id"],
            include_git_context=args.get("include_git_context", True),
            include_patch_bundle=args.get("include_patch_bundle", True),
            include_stash=args.get("include_stash", False),
            reason=args.get("reason"),
            notes=args.get("notes"),
            store_on_server=store_on_server,
        )

        # If stored on server, return lightweight response with handoff_id
        if result.get("stored_on_server"):
            return ToolResponse(
                success=True,
                data={
                    "handoff_id": result.get("handoff_id"),
                    "stored_on_server": True,
                    "package_size_bytes": result.get("package_size_bytes", 0),
                    "has_git_context": result.get("has_git_context", False),
                    "has_patch_bundle": result.get("has_patch_bundle", False),
                    "apply_instructions": result.get("apply_instructions", []),
                    "conflict_hints": result.get("conflict_hints", []),
                    "retrieve_url": result.get("retrieve_url"),
                    "message": (
                        f"Package stored on server ({result.get('package_size_bytes', 0)} bytes). "
                        f"Remote agents can import using: handoff_id='{result.get('handoff_id')}'"
                    ),
                }
            )

        # Gateway returns: success, handoff_id, package_json, package_size_bytes, etc.
        # package_json is a JSON STRING that needs to be passed to import
        package_json = result.get("package_json", "")
        package_size = result.get("package_size_bytes", len(package_json.encode('utf-8')))

        # Parse the package_json to extract metadata for display
        try:
            package_data = json.loads(package_json) if package_json else {}
        except json.JSONDecodeError:
            package_data = {}

        session = package_data.get("session", {})

        # Check if output_file is specified - write to file to avoid truncation
        output_file = args.get("output_file")
        if output_file:
            import os
            # Expand ~ and make absolute
            output_file = os.path.expanduser(output_file)
            if not os.path.isabs(output_file):
                output_file = os.path.abspath(output_file)
            # Ensure directory exists
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            # Write package to file
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(package_json)
            logger.info(f"Session export written to file: {output_file} ({package_size} bytes)")

            return ToolResponse(
                success=True,
                data={
                    "handoff_id": result.get("handoff_id"),
                    "session_id": session.get("session_id", args["session_id"]),
                    "status": "pending",
                    "source_agent_id": session.get("agent_id"),
                    "source_machine_id": session.get("machine_id"),
                    "project_path": session.get("project_path"),
                    "task_description": session.get("task_description"),
                    "has_patch_bundle": result.get("has_patch_bundle", False),
                    "patch_bundle_size_bytes": package_size,
                    "knowledge_refs": [],
                    "created_at": session.get("started_at"),
                    "expires_at": None,
                    # File path instead of inline package_json (prevents truncation)
                    "package_file": output_file,
                    "package_json_truncated": True,
                    "message": f"Package written to {output_file}. Use session_import with package_file parameter.",
                    # Also include apply instructions and conflict hints from gateway
                    "apply_instructions": result.get("apply_instructions", []),
                    "conflict_hints": result.get("conflict_hints", []),
                }
            )

        # Default: return inline (may truncate for large packages)
        # Auto-write to file if package is large (>100KB) to prevent truncation
        if package_size > 100 * 1024:  # 100KB threshold
            import os
            # Auto-generate file path in .claude/handoffs/
            handoffs_dir = os.path.join(
                os.getcwd(), ".claude", "handoffs"
            )
            os.makedirs(handoffs_dir, exist_ok=True)
            auto_file = os.path.join(
                handoffs_dir,
                f"handoff_{result.get('handoff_id', 'unknown')}.json"
            )
            with open(auto_file, 'w', encoding='utf-8') as f:
                f.write(package_json)
            logger.info(f"Large export auto-written to file: {auto_file} ({package_size} bytes)")

            return ToolResponse(
                success=True,
                data={
                    "handoff_id": result.get("handoff_id"),
                    "session_id": session.get("session_id", args["session_id"]),
                    "status": "pending",
                    "source_agent_id": session.get("agent_id"),
                    "source_machine_id": session.get("machine_id"),
                    "project_path": session.get("project_path"),
                    "task_description": session.get("task_description"),
                    "has_patch_bundle": result.get("has_patch_bundle", False),
                    "patch_bundle_size_bytes": package_size,
                    "knowledge_refs": [],
                    "created_at": session.get("started_at"),
                    "expires_at": None,
                    # Auto-saved to file due to size
                    "package_file": auto_file,
                    "package_json_truncated": True,
                    "message": f"Package too large ({package_size} bytes), auto-saved to {auto_file}. Use session_import with package_file parameter.",
                    "apply_instructions": result.get("apply_instructions", []),
                    "conflict_hints": result.get("conflict_hints", []),
                }
            )

        return ToolResponse(
            success=True,
            data={
                "handoff_id": result.get("handoff_id"),
                "session_id": session.get("session_id", args["session_id"]),
                "status": "pending",
                "source_agent_id": session.get("agent_id"),
                "source_machine_id": session.get("machine_id"),
                "project_path": session.get("project_path"),
                "task_description": session.get("task_description"),
                "has_patch_bundle": result.get("has_patch_bundle", False),
                "patch_bundle_size_bytes": package_size,
                "knowledge_refs": [],
                "created_at": session.get("started_at"),
                "expires_at": None,
                # Include the package_json string for import - THIS IS THE KEY FIX
                "package_json": package_json,
                # Also include apply instructions and conflict hints from gateway
                "apply_instructions": result.get("apply_instructions", []),
                "conflict_hints": result.get("conflict_hints", []),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Session not found: {args.get('session_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during session_export: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Session export error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in session_export: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_session_import(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_session_import tool call.

    Imports a session from a handoff package.

    Args:
        args: Tool arguments:
            - package_json: JSON string of the handoff package (from export)
            - package_file: Path to file containing package_json (local agents)
            - handoff_id: Handoff ID to fetch package from server (remote agents)
            - target_directory: Optional target directory override
            - apply_patch: Whether to apply the patch bundle (default: False)

    Priority: handoff_id > package_file > package_json

    Returns:
        ToolResponse with imported session details or error.
    """
    try:
        client = await get_client()

        # Accept handoff_id (remote), package_file (local), or package_json (inline)
        # Priority: handoff_id > package_file > package_json
        handoff_id = args.get("handoff_id")
        package_json = args.get("package_json")
        package_file = args.get("package_file")

        # If handoff_id provided, let the gateway fetch from server storage
        if handoff_id:
            logger.info(f"Importing session from server-stored handoff: {handoff_id}")
            result = await client.import_session(
                handoff_id=handoff_id,
                target_path=args.get("target_directory"),
                apply_patch=args.get("apply_patch", False),
            )
        else:
            # Try package_file first (for local agents)
            if package_file:
                import os
                # Expand ~ and make absolute
                package_file = os.path.expanduser(package_file)
                if not os.path.isabs(package_file):
                    package_file = os.path.abspath(package_file)

                if not os.path.exists(package_file):
                    return ToolResponse(
                        success=False,
                        error=f"Package file not found: {package_file}",
                        error_code="NOT_FOUND"
                    )

                with open(package_file, encoding='utf-8') as f:
                    package_json = f.read()
                logger.info(f"Read package_json from file: {package_file} ({len(package_json)} bytes)")

            if not package_json:
                return ToolResponse(
                    success=False,
                    error="One of handoff_id, package_file, or package_json is required",
                    error_code="VALIDATION_ERROR"
                )

            result = await client.import_session(
                package_json=package_json,
                target_path=args.get("target_directory"),
                apply_patch=args.get("apply_patch", False),
            )

        session = result.get("session", result)
        return ToolResponse(
            success=True,
            data={
                "session_id": session.get("session_id"),
                "status": session.get("status", "resumed"),
                "project_path": session.get("project_path"),
                "task_description": session.get("task_description"),
                "original_session_id": session.get("original_session_id"),
                "source_agent_id": session.get("source_agent_id"),
                "source_machine_id": session.get("source_machine_id"),
                "patch_applied": result.get("patch_applied", False),
                "conflicts_detected": result.get("conflicts_detected", []),
                "imported_at": session.get("started_at"),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during session_import: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBValidationError as e:
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Session import error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in session_import: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_git_context(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_git_context tool call.

    Captures current git context for a session.

    Args:
        args: Tool arguments (session_id, include_diff, include_stash)

    Returns:
        ToolResponse with git context snapshot or error.
    """
    try:
        client = await get_client()

        result = await client.capture_git_context(
            session_id=args["session_id"],
            include_diff=args.get("include_diff", True),
            include_stash=args.get("include_stash", True),
        )

        git_context = result.get("git_context", result)
        uncommitted = git_context.get("uncommitted_changes", [])

        # Format uncommitted changes summary
        changes_summary = {}
        for change in uncommitted:
            change_type = change.get("change_type", "unknown")
            changes_summary[change_type] = changes_summary.get(change_type, 0) + 1

        return ToolResponse(
            success=True,
            data={
                "session_id": args["session_id"],
                "captured_at": git_context.get("captured_at"),
                "branch": git_context.get("branch"),
                "commit_sha": git_context.get("commit_sha"),
                "commit_message": git_context.get("commit_message"),
                "has_uncommitted_changes": git_context.get("has_uncommitted_changes", False),
                "uncommitted_changes_count": len(uncommitted),
                "changes_by_type": changes_summary,
                "untracked_files": git_context.get("untracked_files", []),
                "stash_entries": git_context.get("stash_entries", []),
                "remote_tracking": git_context.get("remote_tracking"),
                "is_dirty": git_context.get("is_dirty", False),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Session not found: {args.get('session_id')}",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during git_context: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Git context error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in git_context: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


# =============================================================================
# ALIAS MANAGEMENT HANDLERS (Phase 5)
# =============================================================================

async def handle_register_alias(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_register_alias tool call.

    Registers a new alias for the current token's team inbox.

    Args:
        args: Tool arguments (alias, role, metadata)

    Returns:
        ToolResponse with created alias record or error.
    """
    try:
        client = await get_client()

        result = await client.register_alias(
            alias=args["alias"],
            role=args.get("role"),
            metadata=args.get("metadata"),
        )

        return ToolResponse(
            success=True,
            data={
                "alias_id": result.get("alias_id"),
                "token_id": result.get("token_id"),
                "alias": result.get("alias"),
                "role": result.get("role"),
                "is_active": result.get("is_active", True),
                "message": result.get("message", f"Alias '{args['alias']}' registered successfully"),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during register_alias: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBValidationError as e:
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except DAKBError as e:
        # Handle 409 Conflict (alias already exists)
        if "already registered" in str(e.message).lower() or "conflict" in str(e.code).lower():
            return ToolResponse(
                success=False,
                error=f"Alias '{args['alias']}' is already registered. Aliases must be globally unique.",
                error_code="ALIAS_CONFLICT"
            )
        logger.error(f"Register alias error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in register_alias: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_list_aliases(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_list_aliases tool call.

    Lists all aliases registered to the current token.

    Args:
        args: Tool arguments (active_only)

    Returns:
        ToolResponse with list of aliases or error.
    """
    try:
        client = await get_client()

        result = await client.list_aliases(
            active_only=args.get("active_only", True),
        )

        # Format aliases for response
        aliases = result.get("aliases", [])
        formatted_aliases = []
        for alias_record in aliases:
            formatted_aliases.append({
                "alias_id": alias_record.get("alias_id"),
                "alias": alias_record.get("alias"),
                "role": alias_record.get("role"),
                "is_active": alias_record.get("is_active", True),
                "registered_at": alias_record.get("registered_at"),
            })

        return ToolResponse(
            success=True,
            data={
                "aliases": formatted_aliases,
                "total": result.get("total", len(formatted_aliases)),
                "token_id": result.get("token_id"),
            }
        )

    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during list_aliases: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"List aliases error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in list_aliases: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_deactivate_alias(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_deactivate_alias tool call.

    Deactivates (soft deletes) an alias.

    Args:
        args: Tool arguments (alias)

    Returns:
        ToolResponse with deactivation confirmation or error.
    """
    try:
        client = await get_client()

        result = await client.deactivate_alias(alias=args["alias"])

        return ToolResponse(
            success=True,
            data={
                "alias": result.get("alias", args["alias"]),
                "message": result.get("message", f"Alias '{args['alias']}' has been deactivated"),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Alias '{args['alias']}' not found",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during deactivate_alias: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBAuthError:
        # Handle 403 Forbidden (not owner of alias)
        return ToolResponse(
            success=False,
            error=f"Access denied: You do not own alias '{args['alias']}'",
            error_code="ACCESS_DENIED"
        )
    except DAKBError as e:
        logger.error(f"Deactivate alias error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in deactivate_alias: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


async def handle_resolve_alias(args: dict[str, Any]) -> ToolResponse:
    """
    Handle dakb_resolve_alias tool call.

    Resolves an alias to its owning token_id.

    Args:
        args: Tool arguments (alias)

    Returns:
        ToolResponse with token_id or error.
    """
    try:
        client = await get_client()

        result = await client.resolve_alias(alias=args["alias"])

        return ToolResponse(
            success=True,
            data={
                "alias": result.get("alias", args["alias"]),
                "token_id": result.get("token_id"),
            }
        )

    except DAKBNotFoundError:
        return ToolResponse(
            success=False,
            error=f"Alias '{args['alias']}' not found or inactive",
            error_code="NOT_FOUND"
        )
    except DAKBTokenExpiredError as e:
        logger.warning(f"Token expired during resolve_alias: {e.message}")
        return ToolResponse(
            success=False,
            error=f"{e.message}. Please re-authenticate.",
            error_code=e.code
        )
    except DAKBError as e:
        logger.error(f"Resolve alias error: {e.message}")
        return ToolResponse(
            success=False,
            error=e.message,
            error_code=e.code
        )
    except Exception as e:
        logger.exception(f"Unexpected error in resolve_alias: {e}")
        return ToolResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_code="INTERNAL_ERROR"
        )


# =============================================================================
# ADVANCED PROXY HANDLER (v1.3 Profile System)
# =============================================================================

# Operation to handler mapping for dakb_advanced proxy tool
ADVANCED_OPERATION_HANDLERS: dict[str, Any] = {
    # Bulk Operations (3)
    "bulk_store": handle_bulk_store,
    "list_by_category": handle_list_by_category,
    "list_by_tags": handle_list_by_tags,
    # Discovery (2)
    "find_related": handle_find_related,
    "cleanup_expired": handle_cleanup_expired,
    # Reputation (4)
    "get_vote_summary": handle_get_vote_summary,
    "get_agent_reputation": handle_get_agent_reputation,
    "get_leaderboard": handle_get_leaderboard,
    "get_my_contributions": handle_get_my_contributions,
    # Moderation (2)
    "flag_for_review": handle_flag_for_review,
    "moderate": handle_moderate,
    # Session Management (6)
    "session_start": handle_session_start,
    "session_status": handle_session_status,
    "session_end": handle_session_end,
    "session_export": handle_session_export,
    "session_import": handle_session_import,
    "git_context": handle_git_context,
    # Alias Management (4)
    "register_alias": handle_register_alias,
    "list_aliases": handle_list_aliases,
    "deactivate_alias": handle_deactivate_alias,
    "resolve_alias": handle_resolve_alias,
}

# Operation parameter documentation for help
# NOTE: Parameter names MUST match actual handler expectations (see handle_* functions)
ADVANCED_OPERATION_PARAMS: dict[str, dict[str, str]] = {
    "bulk_store": {"entries": "array of knowledge entries (required)"},
    "list_by_category": {
        "category": "string (required)",
        "page": "int (optional, default: 1)",
        "page_size": "int (optional, default: 20)",
    },
    "list_by_tags": {
        "tags": "array of strings (required)",
        "match_all": "bool (optional, default: false)",
        "limit": "int (optional, default: 50)",
    },
    "find_related": {
        "knowledge_id": "string (required)",
        "limit": "int (optional, default: 5)",
    },
    "cleanup_expired": {"dry_run": "bool (optional, default: true)"},
    "get_vote_summary": {"knowledge_id": "string (required)"},
    "get_agent_reputation": {"agent_id": "string (optional, defaults to caller)"},
    "get_leaderboard": {
        "metric": "string: reputation|contributions|helpfulness (optional)",
        "limit": "int (optional, default: 10)",
    },
    "get_my_contributions": {},
    "flag_for_review": {
        "knowledge_id": "string (required)",
        "reason": "string: outdated|incorrect|duplicate|spam (required)",
        "details": "string (optional)",
    },
    "moderate": {
        "knowledge_id": "string (required)",
        "action": "string: approve|deprecate|delete (required)",
        "reason": "string (optional)",
    },
    # Session handlers - param names aligned with handle_session_* functions
    "session_start": {
        "project_path": "string (required) - path to project directory",
        "task_description": "string (required) - description of task",
        "objectives": "array of strings (optional) - session objectives",
        "auto_timeout_minutes": "int (optional, default: 30)",
        "metadata": "object (optional) - additional metadata",
    },
    "session_status": {"session_id": "string (required)"},
    "session_end": {
        "session_id": "string (required)",
        "status": "string: completed|abandoned (optional, default: completed)",
    },
    "session_export": {
        "session_id": "string (required)",
        "store_on_server": "bool (optional, default: false) - store on DAKB server for REMOTE agents",
        "output_file": "string (optional) - path to write package JSON (for LOCAL agents)",
        "include_git_context": "bool (optional, default: true)",
        "include_patch_bundle": "bool (optional, default: true)",
        "include_stash": "bool (optional, default: false)",
        "reason": "string (optional) - reason for export",
        "notes": "string (optional) - notes for receiving agent",
        "_note": "REMOTE: use store_on_server=true, returns handoff_id. LOCAL: uses output_file or auto-save >100KB",
    },
    "session_import": {
        "handoff_id": "string (optional) - handoff ID for REMOTE agents (fetched from server)",
        "package_file": "string (optional) - path to file containing package JSON (LOCAL agents)",
        "package_json": "string (optional) - JSON string (only for small packages)",
        "target_directory": "string (optional) - override target directory",
        "apply_patch": "bool (optional, default: false)",
        "_note": "Priority: handoff_id > package_file > package_json. Use handoff_id for remote handoffs.",
    },
    "git_context": {
        "session_id": "string (required)",
        "repository_path": "string (optional)",
        "include_diff_summary": "bool (optional, default: true)",
        "max_diff_size_kb": "int (optional, default: 100)",
    },
    # Alias Management (4)
    "register_alias": {
        "alias": "string (required) - unique alias name (1-50 chars)",
        "role": "string (optional) - role for the alias",
        "metadata": "object (optional) - additional metadata",
    },
    "list_aliases": {
        "active_only": "bool (optional, default: true)",
    },
    "deactivate_alias": {
        "alias": "string (required) - alias name to deactivate",
    },
    "resolve_alias": {
        "alias": "string (required) - alias name to resolve",
    },
}


async def handle_advanced_help(args: dict[str, Any]) -> ToolResponse:
    """
    Return help information about advanced operations.

    If params.operation is specified, returns parameter details for that operation.
    Otherwise, returns list of all available operations.

    Args:
        args: Tool arguments with optional params.operation

    Returns:
        ToolResponse with operation details or list of operations.
    """
    params = args.get("params", {})
    target_op = params.get("operation") if isinstance(params, dict) else None

    if target_op:
        # Help for specific operation
        op_params = ADVANCED_OPERATION_PARAMS.get(target_op)
        if op_params is not None:
            return ToolResponse(
                success=True,
                data={
                    "operation": target_op,
                    "parameters": op_params,
                    "usage": f"dakb_advanced(operation='{target_op}', params={{...}})",
                }
            )
        else:
            available = ", ".join(ADVANCED_OPERATION_HANDLERS.keys())
            return ToolResponse(
                success=False,
                error=f"Unknown operation: {target_op}. Available: {available}",
                error_code="UNKNOWN_OPERATION",
            )

    # General help - list all operations with categories
    return ToolResponse(
        success=True,
        data={
            "available_operations": {
                "bulk": ["bulk_store", "list_by_category", "list_by_tags"],
                "discovery": ["find_related", "cleanup_expired"],
                "reputation": [
                    "get_vote_summary",
                    "get_agent_reputation",
                    "get_leaderboard",
                    "get_my_contributions",
                ],
                "moderation": ["flag_for_review", "moderate"],
                "sessions": [
                    "session_start",
                    "session_status",
                    "session_end",
                    "session_export",
                    "session_import",
                    "git_context",
                ],
                "aliases": [
                    "register_alias",
                    "list_aliases",
                    "deactivate_alias",
                    "resolve_alias",
                ],
            },
            "total_operations": len(ADVANCED_OPERATION_HANDLERS),
            "usage": (
                "Use operation='help' with params={'operation': '<name>'} "
                "to get parameter details for a specific operation."
            ),
        }
    )


async def handle_advanced(args: dict[str, Any]) -> ToolResponse:
    """
    Proxy handler for advanced DAKB operations (v1.3 Profile System).

    Routes to the appropriate handler based on the operation parameter.
    Provides access to 20 advanced operations via a single proxy tool.

    Args:
        args: Tool arguments with operation and params

    Returns:
        ToolResponse from the delegated handler or error.
    """
    operation = args.get("operation")
    params = args.get("params", {})

    # Validation: operation is required
    if not operation:
        return ToolResponse(
            success=False,
            error="'operation' parameter is required",
            error_code="MISSING_OPERATION",
        )

    # Handle help requests
    if operation == "help":
        return await handle_advanced_help(args)

    # Route to handler
    handler = ADVANCED_OPERATION_HANDLERS.get(operation)
    if not handler:
        available = ", ".join(ADVANCED_OPERATION_HANDLERS.keys())
        return ToolResponse(
            success=False,
            error=f"Unknown operation: {operation}. Available: {available}",
            error_code="UNKNOWN_OPERATION",
        )

    logger.info(f"Proxy routing: dakb_advanced -> {operation}")

    # Ensure params is a dict
    if not isinstance(params, dict):
        params = {}

    return await handler(params)


# =============================================================================
# HANDLER DISPATCH
# =============================================================================

HANDLERS: dict[str, Any] = {
    # Basic CRUD (Step 2.1)
    "dakb_store_knowledge": handle_store_knowledge,
    "dakb_search": handle_search,
    "dakb_get_knowledge": handle_get_knowledge,
    "dakb_vote": handle_vote,
    "dakb_status": handle_status,
    # Knowledge Management (Step 2.2)
    "dakb_bulk_store": handle_bulk_store,
    "dakb_list_by_category": handle_list_by_category,
    "dakb_list_by_tags": handle_list_by_tags,
    "dakb_find_related": handle_find_related,
    "dakb_get_stats": handle_get_stats,
    "dakb_cleanup_expired": handle_cleanup_expired,
    # Voting & Reputation (Step 2.3)
    "dakb_get_vote_summary": handle_get_vote_summary,
    "dakb_get_agent_reputation": handle_get_agent_reputation,
    "dakb_get_leaderboard": handle_get_leaderboard,
    "dakb_get_my_contributions": handle_get_my_contributions,
    "dakb_flag_for_review": handle_flag_for_review,
    "dakb_moderate": handle_moderate,
    # Messaging (Phase 3)
    "dakb_send_message": handle_send_message,
    "dakb_get_messages": handle_get_messages,
    "dakb_mark_read": handle_mark_read,
    "dakb_broadcast": handle_broadcast,
    "dakb_get_message_stats": handle_get_message_stats,
    # Session Management (Phase 4)
    "dakb_session_start": handle_session_start,
    "dakb_session_status": handle_session_status,
    "dakb_session_end": handle_session_end,
    "dakb_session_export": handle_session_export,
    "dakb_session_import": handle_session_import,
    "dakb_git_context": handle_git_context,
    # Alias Management (Phase 5)
    "dakb_register_alias": handle_register_alias,
    "dakb_list_aliases": handle_list_aliases,
    "dakb_deactivate_alias": handle_deactivate_alias,
    "dakb_resolve_alias": handle_resolve_alias,
    # Advanced Proxy (v1.3 Profile System)
    "dakb_advanced": handle_advanced,
}


# Tools that should NOT include notification hints (to avoid recursion/noise)
# Only messaging-related tools are exempt to avoid redundant info
_NOTIFICATION_EXEMPT_TOOLS = {
    "dakb_get_messages",
    "dakb_get_message_stats",
    "dakb_mark_read",
}

# Global flag to enable/disable notification hints
_NOTIFICATION_HINTS_ENABLED = os.getenv("DAKB_NOTIFICATION_HINTS", "true").lower() == "true"


async def get_notification_hints() -> NotificationHints | None:
    """
    Get notification summary for the current agent.

    Returns:
        NotificationHints with unread count and hint message, or None on error.
    """
    try:
        client = await get_client()
        if client is None:
            return None

        # Quick query for pending messages (limit to 10 for performance)
        result = await client.get_messages(
            status="pending",
            page=1,
            page_size=10,
        )

        messages = result.get("messages", [])
        total = result.get("total", len(messages))

        if total == 0:
            return None  # No notifications to show

        # Count by priority
        urgent_count = sum(1 for m in messages if m.get("priority") == "urgent")
        high_count = sum(1 for m in messages if m.get("priority") == "high")

        # Generate hint message
        hint_parts = []
        if urgent_count > 0:
            hint_parts.append(f"{urgent_count} urgent")
        if high_count > 0:
            hint_parts.append(f"{high_count} high priority")

        if hint_parts:
            hint = f"You have {', '.join(hint_parts)} message(s). Use dakb_get_messages to view."
        elif total > 0:
            hint = f"You have {total} unread message(s). Use dakb_get_messages to view."
        else:
            hint = None

        return NotificationHints(
            unread=total,
            urgent=urgent_count,
            high=high_count,
            hint=hint,
        )

    except Exception as e:
        logger.debug(f"Failed to get notification hints: {e}")
        return None


async def dispatch_tool(name: str, args: dict[str, Any]) -> ToolResponse:
    """
    Dispatch a tool call to its handler.

    Automatically injects notification hints into responses (unless disabled
    or the tool is exempt).

    Args:
        name: Tool name.
        args: Tool arguments.

    Returns:
        ToolResponse from handler with optional notification hints.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return ToolResponse(
            success=False,
            error=f"Unknown tool: {name}",
            error_code="UNKNOWN_TOOL"
        )

    # Execute the handler
    response = await handler(args)

    # Inject notification hints (unless disabled or exempt)
    if (
        _NOTIFICATION_HINTS_ENABLED
        and name not in _NOTIFICATION_EXEMPT_TOOLS
        and response.success  # Only on success
    ):
        try:
            hints = await get_notification_hints()
            if hints is not None and hints.unread > 0:
                response.notifications = hints
        except Exception as e:
            logger.debug(f"Failed to inject notification hints: {e}")

    return response
