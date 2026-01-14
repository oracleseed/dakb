"""
DAKB Client Data Models

Pydantic models for DAKB API requests and responses.
These models match the DAKB service API contracts.

Version: 1.0.0
Created: 2025-12-17
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


# =============================================================================
# ENUMS
# =============================================================================

class ContentType(str, Enum):
    """Knowledge content type categories."""
    LESSON_LEARNED = "lesson_learned"
    RESEARCH = "research"
    REPORT = "report"
    PATTERN = "pattern"
    CONFIG = "config"
    ERROR_FIX = "error_fix"
    PLAN = "plan"
    IMPLEMENTATION = "implementation"


class Category(str, Enum):
    """Knowledge category for organization."""
    DATABASE = "database"
    ML = "ml"
    TRADING = "trading"
    DEVOPS = "devops"
    SECURITY = "security"
    FRONTEND = "frontend"
    BACKEND = "backend"
    GENERAL = "general"


class AccessLevel(str, Enum):
    """Knowledge access control level."""
    PUBLIC = "public"
    RESTRICTED = "restricted"
    SECRET = "secret"


class MessagePriority(str, Enum):
    """Message priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class MessageStatus(str, Enum):
    """Message delivery status."""
    PENDING = "pending"
    DELIVERED = "delivered"
    READ = "read"
    EXPIRED = "expired"


class VoteType(str, Enum):
    """Vote type for knowledge quality."""
    HELPFUL = "helpful"
    UNHELPFUL = "unhelpful"
    OUTDATED = "outdated"
    INCORRECT = "incorrect"


# =============================================================================
# KNOWLEDGE MODELS
# =============================================================================

class KnowledgeCreate(BaseModel):
    """Request model for creating knowledge entries."""
    title: str = Field(..., max_length=100, description="Brief title for the knowledge")
    content: str = Field(..., min_length=1, description="Knowledge content (markdown supported)")
    content_type: ContentType = Field(..., description="Type of knowledge")
    category: Category = Field(..., description="Category for organization")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence score")
    tags: list[str] = Field(default_factory=list, description="Searchable tags (max 10)")
    related_files: list[str] = Field(default_factory=list, description="Related file paths")
    access_level: AccessLevel = Field(default=AccessLevel.PUBLIC, description="Access control")

    @field_validator('tags')
    @classmethod
    def validate_tags_length(cls, v: list[str]) -> list[str]:
        """Validate that tags list has at most 10 items."""
        if len(v) > 10:
            raise ValueError('tags list cannot exceed 10 items')
        return v


class Knowledge(BaseModel):
    """Full knowledge entry with metadata."""
    knowledge_id: str = Field(..., description="Unique knowledge identifier")
    title: str = Field(..., description="Knowledge title")
    content: str = Field(..., description="Full content")
    content_type: ContentType = Field(..., description="Content type")
    category: Category = Field(..., description="Category")
    confidence: float = Field(..., description="Confidence score")
    tags: list[str] = Field(default=[], description="Tags")
    related_files: list[str] = Field(default=[], description="Related files")
    access_level: AccessLevel = Field(..., description="Access level")
    created_by: str = Field(..., description="Creator agent ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    vote_count: int = Field(default=0, description="Total votes")
    helpful_count: int = Field(default=0, description="Helpful votes")


class SearchResult(BaseModel):
    """Single search result with similarity score."""
    knowledge_id: str = Field(..., description="Knowledge ID")
    title: str = Field(..., description="Title")
    content: str = Field(..., description="Content preview")
    content_type: ContentType = Field(..., description="Content type")
    category: Category = Field(..., description="Category")
    similarity: float = Field(..., description="Similarity score (0-1)")
    tags: list[str] = Field(default=[], description="Tags")
    created_by: str = Field(..., description="Creator")
    created_at: datetime = Field(..., description="Creation time")


class SearchResults(BaseModel):
    """Collection of search results."""
    results: list[SearchResult] = Field(default=[], description="Search results")
    total: int = Field(default=0, description="Total matching results")
    query: str = Field(..., description="Original query")


# =============================================================================
# MESSAGING MODELS
# =============================================================================

class MessageCreate(BaseModel):
    """Request model for sending messages."""
    recipient_id: str = Field(..., description="Target agent ID or alias")
    subject: str = Field(..., max_length=200, description="Message subject")
    content: str = Field(..., min_length=1, description="Message body")
    priority: MessagePriority = Field(default=MessagePriority.NORMAL, description="Priority")
    thread_id: Optional[str] = Field(None, description="Thread ID for replies")
    reply_to_id: Optional[str] = Field(None, description="Message ID being replied to")
    expires_in_hours: int = Field(default=168, ge=1, le=8760, description="Expiration hours")


class Message(BaseModel):
    """Full message with metadata."""
    message_id: str = Field(..., description="Unique message ID")
    sender_id: str = Field(..., description="Sender agent ID")
    recipient_id: str = Field(..., description="Recipient agent ID")
    subject: str = Field(..., description="Subject line")
    content: str = Field(..., description="Message body")
    priority: MessagePriority = Field(..., description="Priority level")
    status: MessageStatus = Field(..., description="Delivery status")
    thread_id: Optional[str] = Field(None, description="Thread ID")
    reply_to_id: Optional[str] = Field(None, description="Reply to message ID")
    created_at: datetime = Field(..., description="Send timestamp")
    expires_at: datetime = Field(..., description="Expiration timestamp")
    read_at: Optional[datetime] = Field(None, description="Read timestamp")
    is_broadcast: bool = Field(default=False, description="Is broadcast message")


class MessageStats(BaseModel):
    """Message statistics for an agent."""
    total_sent: int = Field(default=0, description="Total sent messages")
    total_received: int = Field(default=0, description="Total received messages")
    unread_count: int = Field(default=0, description="Unread messages")
    by_priority: dict[str, int] = Field(default={}, description="Count by priority")
    by_status: dict[str, int] = Field(default={}, description="Count by status")


# =============================================================================
# VOTING MODELS
# =============================================================================

class Vote(BaseModel):
    """Request model for voting on knowledge."""
    knowledge_id: str = Field(..., description="Knowledge ID to vote on")
    vote: VoteType = Field(..., description="Vote type")
    comment: Optional[str] = Field(None, max_length=500, description="Optional comment")
    used_successfully: Optional[bool] = Field(None, description="Was knowledge used successfully")


class VoteResult(BaseModel):
    """Result of a vote operation."""
    success: bool = Field(..., description="Vote recorded successfully")
    knowledge_id: str = Field(..., description="Knowledge ID")
    vote: VoteType = Field(..., description="Vote cast")
    new_vote_count: int = Field(..., description="Updated total votes")
    new_helpful_count: int = Field(..., description="Updated helpful votes")


# =============================================================================
# STATUS AND STATS MODELS
# =============================================================================

class DAKBStatus(BaseModel):
    """DAKB service health status."""
    gateway_status: str = Field(..., description="Gateway status")
    embedding_status: str = Field(..., description="Embedding service status")
    mongodb_status: str = Field(..., description="MongoDB status")
    service: str = Field(default="dakb-gateway", description="Service name")
    version: str = Field(..., description="Service version")


class DAKBStats(BaseModel):
    """DAKB knowledge base statistics."""
    total_entries: int = Field(default=0, description="Total knowledge entries")
    by_category: dict[str, int] = Field(default={}, description="Count by category")
    by_content_type: dict[str, int] = Field(default={}, description="Count by content type")
    by_access_level: dict[str, int] = Field(default={}, description="Count by access level")
    top_tags: list[dict[str, Any]] = Field(default=[], description="Most used tags")


# =============================================================================
# SESSION MODELS
# =============================================================================

class Session(BaseModel):
    """MCP session information."""
    session_id: str = Field(..., description="Session identifier")
    agent_id: str = Field(..., description="Owner agent ID")
    created_at: datetime = Field(..., description="Creation timestamp")
    last_activity: datetime = Field(..., description="Last activity timestamp")
    metadata: dict[str, Any] = Field(default={}, description="Session metadata")


class SessionExport(BaseModel):
    """Exported session data for handoff."""
    session_id: str = Field(..., description="Original session ID")
    agent_id: str = Field(..., description="Original agent ID")
    exported_at: datetime = Field(..., description="Export timestamp")
    context: dict[str, Any] = Field(default={}, description="Session context")
    knowledge_refs: list[str] = Field(default=[], description="Referenced knowledge IDs")
    message_refs: list[str] = Field(default=[], description="Referenced message IDs")


# =============================================================================
# API RESPONSE WRAPPERS
# =============================================================================

class APIResponse(BaseModel):
    """Standard API response wrapper."""
    success: bool = Field(..., description="Operation success")
    data: Optional[dict[str, Any]] = Field(None, description="Response data")
    error: Optional[str] = Field(None, description="Error message if failed")
    error_code: Optional[str] = Field(None, description="Error code if failed")


class PaginatedResponse(BaseModel):
    """Paginated response for list operations."""
    items: list[Any] = Field(default=[], description="Page items")
    page: int = Field(default=1, description="Current page")
    page_size: int = Field(default=20, description="Items per page")
    total: int = Field(default=0, description="Total items")
    has_more: bool = Field(default=False, description="More pages available")


# =============================================================================
# MCP PROTOCOL MODELS
# =============================================================================

class MCPCapabilities(BaseModel):
    """MCP server capabilities."""
    tools: dict[str, Any] = Field(default={}, description="Tool capabilities")
    experimental: dict[str, Any] = Field(default={}, description="Experimental features")


class MCPServerInfo(BaseModel):
    """MCP server information."""
    name: str = Field(..., description="Server name")
    version: str = Field(..., description="Server version")


class MCPInitializeResult(BaseModel):
    """Result of MCP initialize call."""
    protocolVersion: str = Field(..., description="MCP protocol version")
    capabilities: MCPCapabilities = Field(..., description="Server capabilities")
    serverInfo: MCPServerInfo = Field(..., description="Server info")


class MCPTool(BaseModel):
    """MCP tool definition."""
    name: str = Field(..., description="Tool name")
    description: str = Field(default="", description="Tool description")
    inputSchema: dict[str, Any] = Field(default={}, description="JSON Schema for input")


class MCPToolsListResult(BaseModel):
    """Result of tools/list call."""
    tools: list[MCPTool] = Field(default=[], description="Available tools")


class MCPToolCallContent(BaseModel):
    """Content block in tool call result."""
    type: str = Field(default="text", description="Content type")
    text: str = Field(..., description="Content text")


class MCPToolCallResult(BaseModel):
    """Result of tools/call call."""
    content: list[MCPToolCallContent] = Field(default=[], description="Result content")
    isError: bool = Field(default=False, description="Whether result is an error")
