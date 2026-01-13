"""
DAKB Python Client SDK

A Python client library for the DAKB (Distributed Agent Knowledge Base) service.
Provides both synchronous and asynchronous interfaces for knowledge management,
agent messaging, and session handling.

Version: 1.0.0
Created: 2025-12-17
Session: sess_20251217_174528_23429cfe
Phase: 3 - Python Client SDK

Usage:
    # Synchronous client
    from dakb_client import DAKBClient

    client = DAKBClient(base_url="http://localhost:3100", token="your-token")
    result = client.search("machine learning patterns")

    # Asynchronous client
    from dakb_client import DAKBAsyncClient

    async with DAKBAsyncClient(base_url="http://localhost:3100", token="your-token") as client:
        result = await client.search("machine learning patterns")

    # With MCP HTTP transport
    from dakb_client import DAKBMCPClient

    async with DAKBMCPClient(base_url="http://localhost:3100", token="your-token") as client:
        await client.initialize()
        tools = await client.list_tools()
        result = await client.call_tool("dakb_search", {"query": "patterns"})
"""

__version__ = "1.0.0"
__author__ = "DAKB Team"

from .exceptions import (
    DAKBError,
    DAKBConnectionError,
    DAKBAuthenticationError,
    DAKBNotFoundError,
    DAKBValidationError,
    DAKBRateLimitError,
    DAKBServerError,
    DAKBSessionError,
    DAKBTimeoutError,
    DAKBJSONRPCError,
)

from .models import (
    Knowledge,
    KnowledgeCreate,
    SearchResult,
    SearchResults,
    Message,
    MessageCreate,
    MessageStats,
    Vote,
    VoteResult,
    DAKBStatus,
    DAKBStats,
    Session,
    SessionExport,
)

from .client import DAKBClient
from .async_client import DAKBAsyncClient
from .mcp_client import DAKBMCPClient

__all__ = [
    # Version
    "__version__",
    # Exceptions
    "DAKBError",
    "DAKBConnectionError",
    "DAKBAuthenticationError",
    "DAKBNotFoundError",
    "DAKBValidationError",
    "DAKBRateLimitError",
    "DAKBServerError",
    "DAKBSessionError",
    "DAKBTimeoutError",
    "DAKBJSONRPCError",
    # Models
    "Knowledge",
    "KnowledgeCreate",
    "SearchResult",
    "SearchResults",
    "Message",
    "MessageCreate",
    "MessageStats",
    "Vote",
    "VoteResult",
    "DAKBStatus",
    "DAKBStats",
    "Session",
    "SessionExport",
    # Clients
    "DAKBClient",
    "DAKBAsyncClient",
    "DAKBMCPClient",
]
