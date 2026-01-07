"""
DAKB MongoDB Collection Schemas

Pydantic v2 models for all DAKB collections with validation.
These models ensure data integrity and provide type hints for IDE support.

Version: 1.3
Created: 2025-12-07
Author: Backend Agent (Claude Opus 4.5)

Collections:
- dakb_knowledge: Core knowledge repository
- dakb_messages: Cross-agent messaging
- dakb_agents: Agent registry
- dakb_sessions: Session tracking
- dakb_audit_log: Audit trail
"""

import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# =============================================================================
# ENUMS
# =============================================================================

class ContentType(str, Enum):
    """Knowledge content types with associated TTL policies."""
    LESSON_LEARNED = "lesson_learned"  # Never expires
    RESEARCH = "research"               # 1 year
    REPORT = "report"                   # 90 days
    PATTERN = "pattern"                 # Never expires
    CONFIG = "config"                   # 30 days
    ERROR_FIX = "error_fix"             # 180 days


class Category(str, Enum):
    """Knowledge category for filtering and organization."""
    DATABASE = "database"
    ML = "ml"
    TRADING = "trading"
    DEVOPS = "devops"
    SECURITY = "security"
    FRONTEND = "frontend"
    BACKEND = "backend"
    GENERAL = "general"


class ContentFormat(str, Enum):
    """Content format types."""
    MARKDOWN = "markdown"
    JSON = "json"
    CODE = "code"
    PLAIN = "plain"


class CodeLanguage(str, Enum):
    """Programming language for code content."""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    SQL = "sql"
    BASH = "bash"
    JSON = "json"
    YAML = "yaml"
    MARKDOWN = "markdown"


class AccessLevel(str, Enum):
    """3-tier access control levels."""
    PUBLIC = "public"           # Accessible by all agents
    RESTRICTED = "restricted"   # Accessible by specified agents/roles
    SECRET = "secret"           # Highest security, explicit access only


class KnowledgeStatus(str, Enum):
    """Knowledge validation status."""
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    VALIDATED = "validated"
    DEPRECATED = "deprecated"
    DELETED = "deleted"


class AgentType(str, Enum):
    """Types of AI agents/LLMs."""
    CLAUDE = "claude"
    CLAUDE_CODE = "claude_code"
    GPT = "gpt"
    OPENAI = "openai"
    GEMINI = "gemini"
    GROK = "grok"
    LOCAL = "local"
    HUMAN = "human"


class AgentRole(str, Enum):
    """Agent permission roles."""
    ADMIN = "admin"
    DEVELOPER = "developer"
    RESEARCHER = "researcher"
    VIEWER = "viewer"


class AgentStatus(str, Enum):
    """Agent operational status."""
    ACTIVE = "active"
    IDLE = "idle"
    OFFLINE = "offline"
    SUSPENDED = "suspended"


class MessageType(str, Enum):
    """Message types for cross-agent communication."""
    NOTIFICATION = "notification"
    REQUEST = "request"
    RESPONSE = "response"
    ALERT = "alert"


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


class TaskStatus(str, Enum):
    """Session task status."""
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    COMPLETED = "completed"
    HANDED_OFF = "handed_off"


class VoteType(str, Enum):
    """Knowledge voting types."""
    HELPFUL = "helpful"
    UNHELPFUL = "unhelpful"
    OUTDATED = "outdated"
    INCORRECT = "incorrect"


class AuditAction(str, Enum):
    """Audit log action types."""
    KNOWLEDGE_CREATE = "knowledge_create"
    KNOWLEDGE_READ = "knowledge_read"
    KNOWLEDGE_UPDATE = "knowledge_update"
    KNOWLEDGE_DELETE = "knowledge_delete"
    KNOWLEDGE_VALIDATE = "knowledge_validate"
    KNOWLEDGE_VOTE = "knowledge_vote"
    MESSAGE_SEND = "message_send"
    MESSAGE_READ = "message_read"
    AGENT_REGISTER = "agent_register"
    AGENT_HEARTBEAT = "agent_heartbeat"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_HANDOFF = "session_handoff"
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    ACCESS_DENIED = "access_denied"


class ResourceType(str, Enum):
    """Resource types for audit logging."""
    KNOWLEDGE = "knowledge"
    MESSAGE = "message"
    AGENT = "agent"
    SESSION = "session"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def generate_id(prefix: str) -> str:
    """Generate a human-readable unique ID with prefix and timestamp."""
    timestamp = datetime.utcnow().strftime("%Y%m%d")
    unique = uuid.uuid4().hex[:8]
    return f"{prefix}_{timestamp}_{unique}"


# =============================================================================
# EMBEDDED MODELS (Nested Documents)
# =============================================================================

class KnowledgeSource(BaseModel):
    """Source information for knowledge entries."""
    agent_id: str = Field(..., description="Agent that created this knowledge")
    agent_type: AgentType = Field(..., description="Type of AI agent")
    machine_id: str = Field(..., description="Machine identifier")
    session_id: str | None = Field(None, description="Session identifier")
    context: str | None = Field(None, description="Context when knowledge was created")


class VoteDetail(BaseModel):
    """Detailed vote record with agent information."""
    agent_id: str = Field(..., description="Agent that cast the vote")
    vote: VoteType = Field(..., description="Type of vote")
    comment: str | None = Field(None, max_length=500, description="Optional comment")
    used_successfully: bool | None = Field(None, description="Whether knowledge was used successfully")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Votes(BaseModel):
    """Aggregated vote counts."""
    helpful: int = Field(default=0, ge=0)
    unhelpful: int = Field(default=0, ge=0)
    outdated: int = Field(default=0, ge=0)
    incorrect: int = Field(default=0, ge=0)


class MessageAttachment(BaseModel):
    """Attachment for messages."""
    type: str = Field(..., description="Attachment type (json, file, etc.)")
    name: str = Field(..., description="Attachment name")
    content: Any = Field(..., description="Attachment content")


class TodoItem(BaseModel):
    """Todo item for session tracking."""
    task: str = Field(..., description="Task description")
    status: str = Field(default="pending", description="Task status")


class GitContext(BaseModel):
    """
    Git context for session handoff (v1.2).
    Includes full patch bundle for cross-machine reconstruction.
    """
    repository: str = Field(..., description="Repository name")
    branch: str = Field(..., description="Current branch")
    commit_hash: str = Field(..., description="Last commit hash")
    commit_message: str | None = Field(None, description="Last commit message")
    has_uncommitted_changes: bool = Field(default=False)
    uncommitted_files: list[str] = Field(default_factory=list)
    staged_files: list[str] = Field(default_factory=list)
    diff_summary: str | None = Field(None, description="Summary of changes")
    stash_id: str | None = Field(None, description="Stash ID if changes were stashed")
    remote_status: str | None = Field(None, description="Status relative to remote")

    # v1.2: Full content for cross-machine reconstruction
    full_diff: str | None = Field(None, description="Human-readable diff")
    patch_bundle: str | None = Field(None, description="Base64-encoded git patch")
    patch_size_bytes: int | None = Field(None, ge=0, description="Patch size for validation")
    patch_files_count: int | None = Field(None, ge=0, description="Number of files in patch")
    can_apply_cleanly: bool | None = Field(None, description="Whether patch can apply cleanly")

    @field_validator('patch_size_bytes')
    @classmethod
    def validate_patch_size(cls, v: int | None) -> int | None:
        """Validate patch size does not exceed 10MB limit."""
        if v is not None and v > 10 * 1024 * 1024:  # 10MB limit
            raise ValueError("Patch size exceeds 10MB limit")
        return v


class NotificationPreferences(BaseModel):
    """Agent notification preferences by priority."""
    urgent: bool = Field(default=True)
    high: bool = Field(default=True)
    normal: bool = Field(default=False)
    low: bool = Field(default=False)


# =============================================================================
# COLLECTION SCHEMAS
# =============================================================================

class DakbKnowledge(BaseModel):
    """
    Core knowledge repository schema.

    Stores shared knowledge from all agents including lessons learned,
    research findings, code patterns, and error fixes.
    """
    knowledge_id: str = Field(
        default_factory=lambda: generate_id("kn"),
        description="Human-readable unique ID"
    )

    # Content
    title: str = Field(..., max_length=100, description="Brief title for the knowledge")
    content: str = Field(..., description="The knowledge content (markdown supported)")
    content_type: ContentType = Field(..., description="Type of knowledge")
    format: ContentFormat = Field(default=ContentFormat.MARKDOWN, description="Content format")
    code_language: CodeLanguage | None = Field(
        None,
        description="Programming language (required if format is 'code')"
    )

    # Categorization
    category: Category = Field(..., description="Knowledge category")
    tags: list[str] = Field(default_factory=list, max_length=10, description="Searchable tags")
    keywords: list[str] = Field(default_factory=list, description="Additional keywords")

    # Source Information
    source: KnowledgeSource = Field(..., description="Source information")

    # Semantic Search Metadata (vectors stored in FAISS, not MongoDB)
    embedding_indexed: bool = Field(default=False, description="Whether indexed in FAISS")
    embedding_model: str = Field(
        default="all-mpnet-base-v2",
        description="Embedding model used (768-dim)"
    )

    # Access Control
    access_level: AccessLevel = Field(
        default=AccessLevel.PUBLIC,
        description="Access control level"
    )
    allowed_agents: list[str] = Field(
        default_factory=list,
        description="Allowed agents (empty = all for restricted/secret)"
    )
    allowed_roles: list[AgentRole] = Field(
        default_factory=list,
        description="Allowed roles for access"
    )

    # Validation & Quality
    status: KnowledgeStatus = Field(default=KnowledgeStatus.DRAFT)
    validated_by: str | None = Field(None, description="Agent that validated")
    validation_date: datetime | None = Field(None)
    confidence_score: float = Field(default=0.8, ge=0.0, le=1.0)

    # Versioning
    version: int = Field(default=1, ge=1)
    previous_version_id: str | None = Field(None)

    # Lifecycle
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = Field(None, description="None = never expires")

    # Usage Analytics & Voting
    access_count: int = Field(default=0, ge=0)
    last_accessed_at: datetime | None = Field(None)
    last_accessed_by: str | None = Field(None)
    votes: Votes = Field(default_factory=Votes)
    vote_details: list[VoteDetail] = Field(default_factory=list)

    # Related Knowledge
    related_knowledge_ids: list[str] = Field(default_factory=list)
    related_files: list[str] = Field(
        default_factory=list,
        description="Related file paths (e.g., 'src/views.py:123')"
    )

    @model_validator(mode='after')
    def validate_code_language(self) -> 'DakbKnowledge':
        """Ensure code_language is set when format is CODE."""
        if self.format == ContentFormat.CODE and self.code_language is None:
            raise ValueError("code_language is required when format is 'code'")
        return self

    @field_validator('tags')
    @classmethod
    def validate_tags_length(cls, v: list[str]) -> list[str]:
        """Limit tags to 10 items."""
        if len(v) > 10:
            raise ValueError("Maximum 10 tags allowed")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "title": "MongoDB $push vs $set for data append",
                "content": "When appending data to arrays in MongoDB, always use $push...",
                "content_type": "lesson_learned",
                "category": "database",
                "tags": ["mongodb", "data-append", "bug-fix"],
                "source": {
                    "agent_id": "backend",
                    "agent_type": "claude",
                    "machine_id": "mac-m3-local",
                    "session_id": "sess_20251207_xyz"
                },
                "access_level": "public"
            }
        }


class DakbMessage(BaseModel):
    """
    Cross-agent communication schema.

    Enables asynchronous messaging between agents with support for
    direct messages, topic-based pub/sub, and conversation threading.
    """
    message_id: str = Field(
        default_factory=lambda: generate_id("msg"),
        description="Unique message identifier"
    )

    # Routing
    from_agent: str = Field(..., description="Sender agent ID")
    from_machine: str = Field(..., description="Sender machine ID")
    to_agent: str | None = Field(None, description="Target agent (null = broadcast)")
    to_topic: str | None = Field(None, description="Topic for pub/sub")

    # Content
    message_type: MessageType = Field(default=MessageType.NOTIFICATION)
    priority: MessagePriority = Field(default=MessagePriority.NORMAL)
    subject: str = Field(..., max_length=200, description="Message subject")
    body: str = Field(..., description="Message body content")
    attachments: list[MessageAttachment] = Field(default_factory=list)

    # Delivery Status
    status: MessageStatus = Field(default=MessageStatus.PENDING)
    delivered_at: datetime | None = Field(None)
    read_at: datetime | None = Field(None)
    read_by: list[str] = Field(default_factory=list)

    # Lifecycle
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = Field(
        None,
        description="Messages auto-expire (default 7 days)"
    )

    # Threading
    thread_id: str | None = Field(None, description="Thread identifier")
    reply_to: str | None = Field(None, description="Message ID being replied to")

    class Config:
        json_schema_extra = {
            "example": {
                "from_agent": "MLX",
                "from_machine": "ubuntu-server",
                "to_agent": "backend",
                "message_type": "notification",
                "subject": "PPO Training Complete",
                "body": "Training completed with 89% success rate."
            }
        }


class DakbAgent(BaseModel):
    """
    Agent registry schema.

    Tracks all registered agents, their capabilities, and current status.
    """
    # Allow model_version field name (Pydantic v2 protects "model_" namespace by default)
    model_config = ConfigDict(
        protected_namespaces=(),
        json_schema_extra={
            "example": {
                "agent_id": "backend",
                "display_name": "Backend Developer Agent",
                "agent_type": "claude",
                "model_version": "claude-opus-4-5",
                "machine_id": "mac-m3-local",
                "capabilities": ["coding", "debugging", "database"],
                "role": "developer"
            }
        }
    )

    agent_id: str = Field(..., description="Unique agent identifier")

    # Identity
    display_name: str = Field(..., max_length=100)
    agent_type: AgentType = Field(...)
    model_version: str | None = Field(None, description="LLM model version")

    # Location
    machine_id: str = Field(..., description="Machine identifier")
    machine_name: str | None = Field(None, description="Human-readable machine name")
    ip_address: str | None = Field(None, description="For direct communication")
    endpoint_url: str | None = Field(None, description="REST endpoint if available")

    # Capabilities
    capabilities: list[str] = Field(
        default_factory=list,
        description="Agent capabilities (coding, debugging, etc.)"
    )
    specializations: list[str] = Field(
        default_factory=list,
        description="Technical specializations"
    )

    # Access Control
    role: AgentRole = Field(default=AgentRole.DEVELOPER)
    api_key_hash: str | None = Field(None, description="Hashed API key")
    allowed_access_levels: list[AccessLevel] = Field(
        default_factory=lambda: [AccessLevel.PUBLIC]
    )

    # Status
    status: AgentStatus = Field(default=AgentStatus.OFFLINE)
    last_seen: datetime | None = Field(None)
    last_activity: str | None = Field(None, description="Description of last activity")

    # Subscriptions
    subscribed_topics: list[str] = Field(default_factory=list)
    notification_preferences: NotificationPreferences = Field(
        default_factory=NotificationPreferences
    )

    # Statistics
    knowledge_contributed: int = Field(default=0, ge=0)
    messages_sent: int = Field(default=0, ge=0)
    messages_received: int = Field(default=0, ge=0)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DakbSession(BaseModel):
    """
    Session tracking schema.

    Tracks active agent sessions for context preservation and handoff.
    Includes full git context for seamless cross-machine work transfer.
    """
    session_id: str = Field(
        default_factory=lambda: generate_id("sess"),
        description="Unique session identifier"
    )

    agent_id: str = Field(..., description="Agent running the session")
    machine_id: str = Field(..., description="Machine running the session")

    # Task Context
    task_description: str | None = Field(None, description="Current task being worked on")
    task_status: TaskStatus = Field(default=TaskStatus.IN_PROGRESS)

    # Context Preservation
    loaded_contexts: list[str] = Field(
        default_factory=list,
        description="Loaded context categories"
    )
    working_files: list[str] = Field(
        default_factory=list,
        description="Files currently being worked on"
    )
    current_step: str | None = Field(None, description="Current step in task")
    todo_list: list[TodoItem] = Field(default_factory=list)

    # Git Context (v1.2)
    git_context: GitContext | None = Field(
        None,
        description="Full git context for session handoff"
    )

    # Knowledge Generated
    knowledge_ids: list[str] = Field(
        default_factory=list,
        description="Knowledge entries created in this session"
    )

    # Handoff Support
    handed_off_to: str | None = Field(None, description="Agent ID if handed off")
    handoff_notes: str | None = Field(None)
    handoff_timestamp: datetime | None = Field(None)

    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = Field(None)

    class Config:
        json_schema_extra = {
            "example": {
                "agent_id": "backend",
                "machine_id": "mac-m3-local",
                "task_description": "Implementing DAKB MongoDB collections",
                "task_status": "in_progress",
                "working_files": ["schemas.py", "collections.py"]
            }
        }


class DakbAuditLog(BaseModel):
    """
    Audit trail schema.

    Comprehensive logging for security, debugging, and compliance.
    Auto-expires after 90 days.
    """
    log_id: str = Field(
        default_factory=lambda: generate_id("log"),
        description="Unique log entry identifier"
    )

    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Actor
    agent_id: str = Field(..., description="Agent that performed the action")
    machine_id: str | None = Field(None)
    session_id: str | None = Field(None)

    # Action
    action: AuditAction = Field(..., description="Type of action performed")
    resource_type: ResourceType = Field(..., description="Type of resource affected")
    resource_id: str = Field(..., description="ID of the affected resource")

    # Details
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional action details"
    )

    # Security
    ip_address: str | None = Field(None)
    access_level_required: AccessLevel | None = Field(None)
    access_granted: bool = Field(default=True)
    denial_reason: str | None = Field(None)

    # TTL - 90 days expiry
    expires_at: datetime = Field(
        default_factory=lambda: datetime.utcnow() + timedelta(days=90)
    )

    class Config:
        json_schema_extra = {
            "example": {
                "agent_id": "backend",
                "action": "knowledge_create",
                "resource_type": "knowledge",
                "resource_id": "kn_20251207_abc123",
                "details": {"title": "New knowledge created", "category": "database"},
                "access_granted": True
            }
        }


# =============================================================================
# CREATE/UPDATE MODELS (Input validation)
# =============================================================================

class KnowledgeCreate(BaseModel):
    """Schema for creating new knowledge entries."""
    title: str = Field(..., max_length=100)
    content: str = Field(...)
    content_type: ContentType
    category: Category
    format: ContentFormat = ContentFormat.MARKDOWN
    code_language: CodeLanguage | None = None
    tags: list[str] = Field(default_factory=list, max_length=10)
    access_level: AccessLevel = AccessLevel.PUBLIC
    related_files: list[str] = Field(default_factory=list)
    expires_in_days: int | None = Field(None, ge=1)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @model_validator(mode='after')
    def validate_code_language(self) -> 'KnowledgeCreate':
        """Ensure code_language is set when format is CODE."""
        if self.format == ContentFormat.CODE and self.code_language is None:
            raise ValueError("code_language is required when format is 'code'")
        return self


class KnowledgeUpdate(BaseModel):
    """Schema for updating existing knowledge entries."""
    title: str | None = Field(None, max_length=100)
    content: str | None = None
    content_type: ContentType | None = None
    category: Category | None = None
    format: ContentFormat | None = None
    code_language: CodeLanguage | None = None
    tags: list[str] | None = None
    access_level: AccessLevel | None = None
    status: KnowledgeStatus | None = None
    confidence_score: float | None = Field(None, ge=0.0, le=1.0)


class VoteCreate(BaseModel):
    """Schema for voting on knowledge."""
    vote: VoteType
    comment: str | None = Field(None, max_length=500)
    used_successfully: bool | None = None


class MessageCreate(BaseModel):
    """Schema for creating new messages."""
    to_agent: str | None = None
    to_topic: str | None = None
    message_type: MessageType = MessageType.NOTIFICATION
    priority: MessagePriority = MessagePriority.NORMAL
    subject: str = Field(..., max_length=200)
    body: str
    attachments: list[MessageAttachment] = Field(default_factory=list)
    thread_id: str | None = None
    reply_to: str | None = None
    expires_in_days: int = Field(default=7, ge=1, le=365)


class AgentRegister(BaseModel):
    """Schema for registering new agents."""
    model_config = ConfigDict(protected_namespaces=())

    agent_id: str = Field(..., min_length=1, max_length=50)
    display_name: str = Field(..., max_length=100)
    agent_type: AgentType
    model_version: str | None = None
    machine_id: str
    machine_name: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    specializations: list[str] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    """Schema for updating agent information."""
    model_config = ConfigDict(protected_namespaces=())

    display_name: str | None = Field(None, max_length=100)
    model_version: str | None = None
    status: AgentStatus | None = None
    capabilities: list[str] | None = None
    specializations: list[str] | None = None
    subscribed_topics: list[str] | None = None
    notification_preferences: NotificationPreferences | None = None


class SessionCreate(BaseModel):
    """Schema for creating new sessions."""
    task_description: str | None = None
    loaded_contexts: list[str] = Field(default_factory=list)
    working_files: list[str] = Field(default_factory=list)


class SessionUpdate(BaseModel):
    """Schema for updating session information."""
    task_description: str | None = None
    task_status: TaskStatus | None = None
    current_step: str | None = None
    working_files: list[str] | None = None
    git_context: GitContext | None = None


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class KnowledgeResponse(BaseModel):
    """Response model for knowledge queries."""
    knowledge: DakbKnowledge
    similarity_score: float | None = Field(None, description="Similarity score from search")


class SearchResults(BaseModel):
    """Response model for semantic search."""
    results: list[KnowledgeResponse]
    total: int
    query: str
    search_time_ms: float


# =============================================================================
# VOTING & REPUTATION SYSTEM (Step 2.3)
# =============================================================================

class ReputationHistory(BaseModel):
    """Historical record of reputation changes."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    previous_score: float = Field(..., description="Score before change")
    new_score: float = Field(..., description="Score after change")
    change_reason: str = Field(..., description="Reason for change")
    change_source: str | None = Field(None, description="Knowledge ID or event that caused change")


class AgentReputation(BaseModel):
    """
    Agent reputation model for voting weight and quality scoring.

    Reputation Formula: rep = (knowledge_count * 10) + (helpful_received * 5) - (unhelpful_received * 2)
    Vote Weight Formula: weight = 1 + (reputation / 1000), capped at 3x
    """
    agent_id: str = Field(..., description="Agent identifier")

    # Core reputation metrics
    reputation_score: float = Field(default=0.0, description="Calculated reputation score")

    # Contributing factors
    knowledge_contributed: int = Field(default=0, ge=0, description="Total knowledge entries created")
    helpful_votes_received: int = Field(default=0, ge=0, description="Helpful votes on agent's knowledge")
    unhelpful_votes_received: int = Field(default=0, ge=0, description="Unhelpful votes on agent's knowledge")
    outdated_votes_received: int = Field(default=0, ge=0, description="Outdated votes on agent's knowledge")
    incorrect_votes_received: int = Field(default=0, ge=0, description="Incorrect votes on agent's knowledge")

    # Voting activity
    votes_cast: int = Field(default=0, ge=0, description="Total votes cast by this agent")
    votes_cast_helpful: int = Field(default=0, ge=0, description="Helpful votes cast")
    votes_cast_unhelpful: int = Field(default=0, ge=0, description="Unhelpful votes cast")

    # Derived metrics
    accuracy_rate: float = Field(default=1.0, ge=0.0, le=1.0, description="Rate of non-incorrect knowledge")
    helpfulness_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Rate of helpful knowledge")

    # History tracking
    reputation_history: list[ReputationHistory] = Field(
        default_factory=list,
        description="Historical reputation changes (last 100)"
    )

    # Timestamps
    first_contribution_at: datetime | None = Field(None, description="When agent first contributed")
    last_contribution_at: datetime | None = Field(None, description="Most recent contribution")
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def calculate_vote_weight(self) -> float:
        """
        Calculate vote weight based on reputation.

        ISS-049 Fix: Agents with negative reputation get reduced vote weight.

        Formula:
        - Reputation >= 0: weight = 1.0 + (reputation / 1000), capped at 3.0x
        - Reputation < 0: weight = max(0.5, 1.0 + (reputation / 1000)), min 0.5x

        Returns:
            Vote weight multiplier (0.5 to 3.0)
        """
        weight = 1.0 + (self.reputation_score / 1000.0)

        # ISS-049: Different caps for positive vs negative reputation
        if self.reputation_score >= 0:
            # Positive reputation: cap between 1.0 and 3.0
            return min(max(weight, 1.0), 3.0)
        else:
            # Negative reputation: cap between 0.5 and 1.0
            # Minimum weight is 0.5 to prevent total vote silencing
            return min(max(weight, 0.5), 1.0)

    def calculate_reputation(self) -> float:
        """
        Calculate reputation score from contributing factors.

        Formula: rep = (knowledge_count * 10) + (helpful_received * 5) - (unhelpful_received * 2)

        Returns:
            Calculated reputation score
        """
        return (
            (self.knowledge_contributed * 10)
            + (self.helpful_votes_received * 5)
            - (self.unhelpful_votes_received * 2)
        )


class KnowledgeQuality(BaseModel):
    """
    Knowledge quality scoring model.

    Quality Formula: quality = (weighted_helpful - weighted_unhelpful + (outdated_count * -3) + (incorrect_count * -5)) / total_votes
    Auto-deprecation threshold: quality < -0.5

    ISS-046 Fix: Use raw counts for outdated/incorrect penalties, not weighted sums.
    This prevents double-applying the penalty (once in weighting, once in formula).
    """
    knowledge_id: str = Field(..., description="Knowledge identifier")

    # Vote counts (weighted)
    weighted_helpful: float = Field(default=0.0, description="Sum of weighted helpful votes")
    weighted_unhelpful: float = Field(default=0.0, description="Sum of weighted unhelpful votes")
    weighted_outdated: float = Field(default=0.0, description="Sum of weighted outdated votes")
    weighted_incorrect: float = Field(default=0.0, description="Sum of weighted incorrect votes")

    # ISS-046: Raw counts for penalty calculation (prevents double-penalty)
    outdated_count: int = Field(default=0, ge=0, description="Raw count of outdated votes")
    incorrect_count: int = Field(default=0, ge=0, description="Raw count of incorrect votes")

    # Raw counts
    total_votes: int = Field(default=0, ge=0, description="Total number of votes")

    # Calculated score
    quality_score: float = Field(default=0.0, description="Calculated quality score")

    # Usage metrics
    usage_count: int = Field(default=0, ge=0, description="Times used successfully")

    # Flags
    flagged_for_review: bool = Field(default=False, description="Whether flagged for review")
    auto_deprecation_warning: bool = Field(default=False, description="Quality below threshold")

    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def calculate_quality(self) -> float:
        """
        Calculate quality score from weighted votes.

        ISS-046 Fix: Use raw counts for outdated/incorrect penalties.
        Formula: quality = (weighted_helpful - weighted_unhelpful + (outdated_count * -3) + (incorrect_count * -5)) / total_votes

        Returns:
            Calculated quality score
        """
        if self.total_votes == 0:
            return 0.0

        # ISS-046: Use raw counts for penalty multipliers, not weighted sums
        # This prevents double-applying penalties (once in weighting, once in formula)
        score = (
            self.weighted_helpful
            - self.weighted_unhelpful
            + (self.outdated_count * -3)
            + (self.incorrect_count * -5)
        ) / self.total_votes

        return score

    def should_auto_deprecate(self) -> bool:
        """
        Check if knowledge should be auto-deprecated.

        Threshold: quality_score < -0.5

        Returns:
            True if should be deprecated
        """
        return self.quality_score < -0.5


class FlagReason(str, Enum):
    """Reasons for flagging knowledge for review."""
    OUTDATED = "outdated"
    INCORRECT = "incorrect"
    DUPLICATE = "duplicate"
    SPAM = "spam"


class ModerateAction(str, Enum):
    """Actions available for moderation."""
    APPROVE = "approve"
    DEPRECATE = "deprecate"
    DELETE = "delete"


class KnowledgeFlag(BaseModel):
    """Flag for knowledge review."""
    flag_id: str = Field(
        default_factory=lambda: generate_id("flag"),
        description="Unique flag identifier"
    )
    knowledge_id: str = Field(..., description="Flagged knowledge ID")
    flagged_by: str = Field(..., description="Agent who flagged")
    reason: FlagReason = Field(..., description="Reason for flagging")
    details: str | None = Field(None, max_length=500, description="Additional details")
    status: str = Field(default="pending", description="Flag status: pending, reviewed, resolved")
    reviewed_by: str | None = Field(None, description="Moderator who reviewed")
    reviewed_at: datetime | None = Field(None)
    resolution: str | None = Field(None, description="Resolution action taken")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class VoteSummary(BaseModel):
    """Summary of votes for a knowledge entry."""
    knowledge_id: str = Field(..., description="Knowledge identifier")
    helpful: int = Field(default=0, ge=0)
    unhelpful: int = Field(default=0, ge=0)
    outdated: int = Field(default=0, ge=0)
    incorrect: int = Field(default=0, ge=0)
    quality_score: float = Field(default=0.0)
    vote_history: list[VoteDetail] = Field(default_factory=list)
    total_votes: int = Field(default=0, ge=0)


class LeaderboardEntry(BaseModel):
    """Entry in the agent leaderboard."""
    rank: int = Field(..., ge=1, description="Leaderboard rank")
    agent_id: str = Field(..., description="Agent identifier")
    score: float = Field(..., description="Score for the metric")
    metric: str = Field(..., description="Metric being ranked")


class AgentContributions(BaseModel):
    """Summary of an agent's contributions."""
    agent_id: str = Field(..., description="Agent identifier")
    knowledge_entries: list[dict] = Field(default_factory=list, description="Knowledge entries created")
    votes_cast: list[dict] = Field(default_factory=list, description="Votes cast by agent")
    reputation_history: list[ReputationHistory] = Field(default_factory=list)
    reputation_score: float = Field(default=0.0)
    rank: int | None = Field(None, description="Current rank")
    total_knowledge: int = Field(default=0)
    total_votes: int = Field(default=0)


# =============================================================================
# AGENT ALIAS SYSTEM (Token Team with Agent Aliases)
# =============================================================================

class DakbAgentAlias(BaseModel):
    """
    Agent alias for token team routing.

    Allows one token (e.g., 'claude-code-agent') to register multiple aliases
    (e.g., 'Coordinator', 'Reviewer', 'Leader'). Messages to any alias route
    to the token owner's shared inbox.

    The alias system enables:
    - Multiple persona/role representation for a single token
    - Shared inbox routing for all aliases under a token
    - Role-based identity switching without multiple tokens
    - Backwards-compatible with existing agent_id routing

    Example:
        Token 'claude-code-agent' registers aliases:
        - 'Coordinator' (role: orchestration)
        - 'Reviewer' (role: code_review)
        - 'Backend' (role: implementation)

        Messages to any of these aliases route to 'claude-code-agent' inbox.
    """
    alias_id: str = Field(
        default_factory=lambda: generate_id("alias"),
        description="Unique alias identifier"
    )
    token_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Primary token identity (owner of the alias)"
    )
    alias: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Alias name (globally unique across all tokens)"
    )
    role: str | None = Field(
        None,
        max_length=100,
        description="Optional role metadata (e.g., 'orchestration', 'code_review')"
    )
    registered_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the alias was registered"
    )
    registered_by: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Must match token_id (same source) for security"
    )
    is_active: bool = Field(
        default=True,
        description="Whether the alias is currently active"
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata for the alias (e.g., display_name, description)"
    )

    @model_validator(mode='after')
    def validate_registered_by_matches_token(self) -> 'DakbAgentAlias':
        """Ensure registered_by matches token_id for security."""
        if self.registered_by != self.token_id:
            raise ValueError(
                f"registered_by ('{self.registered_by}') must match token_id ('{self.token_id}'). "
                "Aliases can only be registered by their owning token."
            )
        return self

    class Config:
        json_schema_extra = {
            "example": {
                "alias_id": "alias_20251211_abc123",
                "token_id": "claude-code-agent",
                "alias": "Coordinator",
                "role": "orchestration",
                "registered_at": "2025-12-11T10:00:00Z",
                "registered_by": "claude-code-agent",
                "is_active": True,
                "metadata": {
                    "display_name": "Agent Coordinator",
                    "description": "Orchestrates multi-agent workflows"
                }
            }
        }


class AliasCreate(BaseModel):
    """Schema for registering a new agent alias."""
    alias: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Alias name (must be globally unique)"
    )
    role: str | None = Field(
        None,
        max_length=100,
        description="Optional role for the alias"
    )
    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata"
    )


class AliasUpdate(BaseModel):
    """Schema for updating an existing alias."""
    role: str | None = Field(None, max_length=100)
    is_active: bool | None = None
    metadata: dict | None = None
