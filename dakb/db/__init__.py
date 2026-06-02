"""
DAKB Database Layer
===================

MongoDB repositories and Pydantic schemas for DAKB data operations.

Components:
- schemas.py: Pydantic v2 models and enums
- collections.py: MongoDB client and collection operations
- indexes.py: Index creation and management
- repositories/: Domain-specific repository classes
- whiteboard_schemas.py / whiteboard_repository.py / whiteboard_indexes.py: Whiteboard

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

# Import admin schemas
from .admin_schemas import (
    AdminAgentAdd,
    AdminAgentEntry,
    AdminAgentListResponse,
    AdminAgentRemove,
    AdminConfigCreate,
    AdminConfigDocument,
    ConfigValueType,
    DEFAULT_ADMIN_AGENTS,
    DEFAULT_RUNTIME_CONFIGS,
    RuntimeConfigCreate,
    RuntimeConfigDocument,
    RuntimeConfigListResponse,
    RuntimeConfigUpdate,
    TokenRefreshRequest,
    TokenRegistryCreate,
    TokenRegistryDocument,
    TokenRegistryListResponse,
    TokenRegistryResponse,
    TokenRevokeRequest,
    TokenStatus,
    hash_token,
    utcnow,
)

# Import schemas
from .schemas import (
    AccessLevel,
    AgentRegister,
    AgentRole,
    AgentStatus,
    AgentType,
    AgentUpdate,
    AliasCreate,
    AliasUpdate,
    AllowedMimeType,
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
    DakbTask,
    DakbTaskCreate,
    DelegatedTaskStatus,
    FlagReason,
    GitContext,
    KnowledgeCreate,
    KnowledgeFlag,
    KnowledgeQuality,
    KnowledgeResponse,
    KnowledgeSource,
    KnowledgeStatus,
    KnowledgeUpdate,
    KnowledgeVersion,
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
    ThreadPost,
    ThreadPostCreate,
    ThreadPostStatus,
    ThreadPostType,
    ThreadSummary,
    TodoItem,
    VaultFile,
    VaultFileStatus,
    VaultHold,
    VoteCreate,
    VoteDetail,
    Votes,
    VoteSummary,
    VoteType,
    generate_id,
)

# Import whiteboard schemas
from .whiteboard_schemas import (
    BoardSettings,
    BoardType,
    SectionPriority,
    SectionStatus,
    SectionType,
    SnapshotTrigger,
    SnapshotType,
    WhiteboardBoard,
    WhiteboardBoardCreate,
    WhiteboardSection,
    WhiteboardSectionCreate,
    WhiteboardSectionUpdate,
    WhiteboardSnapshot,
    WhiteboardSnapshotCreate,
)

# Import collections
from .collections import (
    ThreadRepository,
    VersionRepository,
    get_dakb_client,
    get_dakb_repositories,
)

# Import repositories
from .repositories.vault_repository import VaultFileRepository
from .whiteboard_repository import (
    VersionConflictError,
    WhiteboardRepository,
)

__all__ = [
    # Admin Schemas
    "AdminAgentAdd",
    "AdminAgentEntry",
    "AdminAgentListResponse",
    "AdminAgentRemove",
    "AdminConfigCreate",
    "AdminConfigDocument",
    "ConfigValueType",
    "DEFAULT_ADMIN_AGENTS",
    "DEFAULT_RUNTIME_CONFIGS",
    "RuntimeConfigCreate",
    "RuntimeConfigDocument",
    "RuntimeConfigListResponse",
    "RuntimeConfigUpdate",
    "TokenRefreshRequest",
    "TokenRegistryCreate",
    "TokenRegistryDocument",
    "TokenRegistryListResponse",
    "TokenRegistryResponse",
    "TokenRevokeRequest",
    "TokenStatus",
    "hash_token",
    "utcnow",
    # Enums
    "AccessLevel",
    "AgentRole",
    "AgentStatus",
    "AgentType",
    "AllowedMimeType",
    "AuditAction",
    "BoardType",
    "Category",
    "CodeLanguage",
    "ContentFormat",
    "ContentType",
    "DelegatedTaskStatus",
    "FlagReason",
    "KnowledgeStatus",
    "MessagePriority",
    "MessageStatus",
    "MessageType",
    "ModerateAction",
    "ResourceType",
    "SectionPriority",
    "SectionStatus",
    "SectionType",
    "SnapshotTrigger",
    "SnapshotType",
    "TaskStatus",
    "ThreadPostStatus",
    "ThreadPostType",
    "VaultFileStatus",
    "VoteType",
    # Models
    "AgentRegister",
    "AgentUpdate",
    "AliasCreate",
    "AliasUpdate",
    "BoardSettings",
    "DakbAgent",
    "DakbAgentAlias",
    "DakbAuditLog",
    "DakbKnowledge",
    "DakbMessage",
    "DakbSession",
    "DakbTask",
    "DakbTaskCreate",
    "GitContext",
    "KnowledgeCreate",
    "KnowledgeFlag",
    "KnowledgeQuality",
    "KnowledgeResponse",
    "KnowledgeSource",
    "KnowledgeUpdate",
    "KnowledgeVersion",
    "LeaderboardEntry",
    "MessageAttachment",
    "MessageCreate",
    "NotificationPreferences",
    "ReputationHistory",
    "SearchResults",
    "SessionCreate",
    "SessionUpdate",
    "ThreadPost",
    "ThreadPostCreate",
    "ThreadSummary",
    "TodoItem",
    "VaultFile",
    "VaultHold",
    "VoteCreate",
    "VoteDetail",
    "Votes",
    "VoteSummary",
    "WhiteboardBoard",
    "WhiteboardBoardCreate",
    "WhiteboardSection",
    "WhiteboardSectionCreate",
    "WhiteboardSectionUpdate",
    "WhiteboardSnapshot",
    "WhiteboardSnapshotCreate",
    # Repositories
    "ThreadRepository",
    "VaultFileRepository",
    "VersionConflictError",
    "VersionRepository",
    "WhiteboardRepository",
    # Functions
    "generate_id",
    "get_dakb_client",
    "get_dakb_repositories",
]
