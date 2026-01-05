"""
DAKB Client Exceptions

Custom exception classes for DAKB client operations.
Provides granular error handling for different failure scenarios.

Version: 1.0.0
Created: 2025-12-17
"""

from typing import Any, Optional


class DAKBError(Exception):
    """Base exception for all DAKB client errors."""

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code or "DAKB_ERROR"
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.code}: {self.message} - {self.details}"
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for serialization."""
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class DAKBConnectionError(DAKBError):
    """
    Raised when unable to connect to DAKB service.

    Common causes:
    - Service not running
    - Network issues
    - Incorrect URL
    """

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, "CONNECTION_ERROR", details)


class DAKBAuthenticationError(DAKBError):
    """
    Raised when authentication fails.

    Common causes:
    - Invalid or expired token
    - Missing Authorization header
    - Revoked agent credentials
    """

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, "AUTH_ERROR", details)


class DAKBNotFoundError(DAKBError):
    """
    Raised when requested resource is not found.

    Common causes:
    - Invalid knowledge ID
    - Message ID doesn't exist
    - Session expired or invalid
    """

    def __init__(
        self,
        message: str,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
    ):
        details = {}
        if resource_type:
            details["resource_type"] = resource_type
        if resource_id:
            details["resource_id"] = resource_id
        super().__init__(message, "NOT_FOUND", details)


class DAKBValidationError(DAKBError):
    """
    Raised when request validation fails.

    Common causes:
    - Missing required fields
    - Invalid field values
    - Schema violations
    """

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        constraint: Optional[str] = None,
    ):
        details = {}
        if field:
            details["field"] = field
        if constraint:
            details["constraint"] = constraint
        super().__init__(message, "VALIDATION_ERROR", details)


class DAKBRateLimitError(DAKBError):
    """
    Raised when rate limit is exceeded.

    Includes retry information when available.
    """

    def __init__(
        self,
        message: str,
        retry_after: Optional[int] = None,
        limit: Optional[int] = None,
    ):
        details = {}
        if retry_after is not None:
            details["retry_after_seconds"] = retry_after
        if limit is not None:
            details["limit"] = limit
        super().__init__(message, "RATE_LIMIT", details)
        self.retry_after = retry_after


class DAKBServerError(DAKBError):
    """
    Raised when server encounters an internal error.

    Usually indicates a bug or service issue.
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_id: Optional[str] = None,
    ):
        details = {}
        if status_code:
            details["status_code"] = status_code
        if error_id:
            details["error_id"] = error_id
        super().__init__(message, "SERVER_ERROR", details)


class DAKBSessionError(DAKBError):
    """
    Raised when MCP session operations fail.

    Common causes:
    - Session not initialized
    - Session expired
    - Session ownership mismatch
    - Too many concurrent sessions
    """

    def __init__(
        self,
        message: str,
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        details = {}
        if session_id:
            details["session_id"] = session_id
        if reason:
            details["reason"] = reason
        super().__init__(message, "SESSION_ERROR", details)


class DAKBTimeoutError(DAKBError):
    """
    Raised when a request times out.
    """

    def __init__(
        self,
        message: str,
        timeout_seconds: Optional[float] = None,
        operation: Optional[str] = None,
    ):
        details = {}
        if timeout_seconds is not None:
            details["timeout_seconds"] = timeout_seconds
        if operation:
            details["operation"] = operation
        super().__init__(message, "TIMEOUT", details)


class DAKBJSONRPCError(DAKBError):
    """
    Raised when JSON-RPC response contains an error.

    Maps standard JSON-RPC error codes:
    - -32700: Parse error
    - -32600: Invalid request
    - -32601: Method not found
    - -32602: Invalid params
    - -32603: Internal error
    - -32000 to -32099: Server errors
    """

    # Standard JSON-RPC error codes
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    def __init__(
        self,
        code: int,
        message: str,
        data: Optional[dict[str, Any]] = None,
        request_id: Optional[int | str] = None,
    ):
        self.jsonrpc_code = code
        self.data = data
        self.request_id = request_id

        error_code = self._map_code(code)
        details = {"jsonrpc_code": code}
        if data:
            details["data"] = data
        if request_id is not None:
            details["request_id"] = request_id

        super().__init__(message, error_code, details)

    @staticmethod
    def _map_code(code: int) -> str:
        """Map JSON-RPC code to error code string."""
        mapping = {
            -32700: "JSONRPC_PARSE_ERROR",
            -32600: "JSONRPC_INVALID_REQUEST",
            -32601: "JSONRPC_METHOD_NOT_FOUND",
            -32602: "JSONRPC_INVALID_PARAMS",
            -32603: "JSONRPC_INTERNAL_ERROR",
            -32000: "JSONRPC_SERVER_ERROR",
            -32001: "JSONRPC_SESSION_ERROR",
            -32002: "JSONRPC_AUTH_ERROR",
            -32003: "JSONRPC_TOOL_ERROR",
        }
        return mapping.get(code, f"JSONRPC_ERROR_{abs(code)}")
