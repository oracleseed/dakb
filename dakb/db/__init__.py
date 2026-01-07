"""
DAKB Database Layer
===================

MongoDB repositories and Pydantic schemas for DAKB data operations.

Components:
- schemas.py: Pydantic v2 models and enums
- collections.py: MongoDB client and collection operations
- indexes.py: Index creation and management
- repositories/: Domain-specific repository classes

Collections:
- dakb_knowledge: Core knowledge entries
- dakb_messages: Cross-agent messages
- dakb_agents: Agent registry
- dakb_agent_aliases: Alias-to-agent mappings
- dakb_sessions: Work session tracking
- dakb_registration_invites: Self-registration tokens
- dakb_votes: Knowledge voting records
- dakb_audit_log: Security audit trail

Usage:
    from dakb.db import KnowledgeCreate, Category, ContentType
    from dakb.db import get_dakb_repositories

    # Create knowledge entry
    entry = KnowledgeCreate(
        title="My insight",
        content="Something I learned...",
        content_type=ContentType.LESSON_LEARNED,
        category=Category.GENERAL
    )
"""

# Import schemas
from .schemas import (
    AccessLevel,
    AgentRegister,
    AgentRole,
    AgentStatus,
    AgentType,
    AgentUpdate,
    AuditAction,
    Category,
    CodeLanguage,
    ContentFormat,
    ContentType,
    DakbAgent,
    DakbAgentAlias,
    DakbAuditLog,
    DakbKnowledge,
    DakbMessage,
    DakbSession,
    FlagReason,
    GitContext,
    KnowledgeCreate,
    KnowledgeFlag,
    KnowledgeQuality,
    KnowledgeResponse,
    KnowledgeSource,
    KnowledgeStatus,
    KnowledgeUpdate,
    LeaderboardEntry,
    MessageAttachment,
    MessageCreate,
    MessagePriority,
    MessageStatus,
    MessageType,
    ModerateAction,
    NotificationPreferences,
    ReputationHistory,
    ResourceType,
    SearchResults,
    SessionCreate,
    SessionUpdate,
    TaskStatus,
    TodoItem,
    VoteCreate,
    VoteDetail,
    Votes,
    VoteSummary,
    VoteType,
    generate_id,
)

# Import collections
from .collections import (
    get_dakb_client,
    get_dakb_repositories,
)

__all__ = [
    # Enums
    "AccessLevel",
    "AgentRole",
    "AgentStatus",
    "AgentType",
    "AuditAction",
    "Category",
    "CodeLanguage",
    "ContentFormat",
    "ContentType",
    "FlagReason",
    "KnowledgeStatus",
    "MessagePriority",
    "MessageStatus",
    "MessageType",
    "ModerateAction",
    "ResourceType",
    "TaskStatus",
    "VoteType",
    # Models
    "AgentRegister",
    "AgentUpdate",
    "DakbAgent",
    "DakbAgentAlias",
    "DakbAuditLog",
    "DakbKnowledge",
    "DakbMessage",
    "DakbSession",
    "GitContext",
    "KnowledgeCreate",
    "KnowledgeFlag",
    "KnowledgeQuality",
    "KnowledgeResponse",
    "KnowledgeSource",
    "KnowledgeUpdate",
    "LeaderboardEntry",
    "MessageAttachment",
    "MessageCreate",
    "NotificationPreferences",
    "ReputationHistory",
    "SearchResults",
    "SessionCreate",
    "SessionUpdate",
    "TodoItem",
    "VoteCreate",
    "VoteDetail",
    "Votes",
    "VoteSummary",
    # Functions
    "generate_id",
    "get_dakb_client",
    "get_dakb_repositories",
]
