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
    from dakb.db.schemas import KnowledgeCreate, Category, ContentType
    from dakb.db.collections import KnowledgeCollection

    # Create knowledge entry
    entry = KnowledgeCreate(
        title="My insight",
        content="Something I learned...",
        content_type=ContentType.LESSON_LEARNED,
        category=Category.GENERAL
    )
"""

__all__ = [
    "schemas",
    "collections",
]
