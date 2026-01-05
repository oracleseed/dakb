"""
DAKB MongoDB Collection Operations

Collection initialization and CRUD operations for all DAKB collections.
Uses the project's existing MongoDB connection via settings.get_mongo_client().

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

import logging
from datetime import datetime, timedelta
from typing import Optional, Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, PyMongoError

from .schemas import (
    # Main schemas
    DakbKnowledge,
    DakbMessage,
    DakbAgent,
    DakbSession,
    DakbAuditLog,
    # Create/Update schemas
    KnowledgeCreate,
    KnowledgeUpdate,
    VoteCreate,
    MessageCreate,
    AgentRegister,
    AgentUpdate,
    SessionCreate,
    SessionUpdate,
    # Embedded models
    KnowledgeSource,
    VoteDetail,
    Votes,
    # Enums
    AccessLevel,
    KnowledgeStatus,
    AgentStatus,
    MessageStatus,
    TaskStatus,
    AuditAction,
    ResourceType,
    VoteType,
    # Reputation & Voting (Step 2.3)
    AgentReputation,
    KnowledgeQuality,
    KnowledgeFlag,
    VoteSummary,
    LeaderboardEntry,
    AgentContributions,
    ReputationHistory,
    FlagReason,
    ModerateAction,
    # Agent Alias System (Token Team)
    DakbAgentAlias,
    AliasCreate,
    AliasUpdate,
)

# Import registration schemas and repositories (Self-Registration v1.0)
from .registration_schemas.registration import (
    DakbInviteToken,
    DakbRegistrationAudit,
    InviteTokenCreate,
    InviteTokenStatus,
    RegistrationAuditAction,
)
from .repositories.registration import (
    InviteTokenRepository,
    RegistrationAuditRepository,
)

logger = logging.getLogger(__name__)


# =============================================================================
# MONGODB CONNECTION HELPER
# =============================================================================

def get_dakb_client():
    """
    Get MongoDB client for DAKB operations.

    Connects to MongoDB using the MONGO_URI environment variable.
    Defaults to localhost if not configured.

    Returns:
        MongoClient: Configured MongoDB client instance

    Example:
        >>> client = get_dakb_client()
        >>> repos = get_dakb_repositories(client)
        >>> knowledge = repos["knowledge"].get_by_id("kn_20251207_abc123")
    """
    import os
    from pymongo import MongoClient

    mongo_uri = os.getenv('MONGO_URI', 'mongodb://localhost:27017/dakb')
    return MongoClient(mongo_uri)


# Collection names
COLLECTION_KNOWLEDGE = "dakb_knowledge"
COLLECTION_MESSAGES = "dakb_messages"
COLLECTION_AGENTS = "dakb_agents"
COLLECTION_SESSIONS = "dakb_sessions"
COLLECTION_AUDIT_LOG = "dakb_audit_log"
# Step 2.3: Voting & Reputation
COLLECTION_REPUTATION = "dakb_agent_reputation"
COLLECTION_QUALITY = "dakb_knowledge_quality"
COLLECTION_FLAGS = "dakb_knowledge_flags"
# Agent Alias System (Token Team)
COLLECTION_ALIASES = "dakb_agent_aliases"
# Self-Registration v1.0 (Invite-Only)
COLLECTION_INVITE_TOKENS = "dakb_invite_tokens"
COLLECTION_REGISTRATION_AUDIT = "dakb_registration_audit"

ALL_COLLECTIONS = [
    COLLECTION_KNOWLEDGE,
    COLLECTION_MESSAGES,
    COLLECTION_AGENTS,
    COLLECTION_SESSIONS,
    COLLECTION_AUDIT_LOG,
    COLLECTION_REPUTATION,
    COLLECTION_QUALITY,
    COLLECTION_FLAGS,
    COLLECTION_ALIASES,
    COLLECTION_INVITE_TOKENS,
    COLLECTION_REGISTRATION_AUDIT,
]


class DAKBCollections:
    """
    Manager for DAKB MongoDB collections.

    Provides collection initialization and basic operations.
    Use the specialized repository classes for CRUD operations.
    """

    def __init__(self, mongo_client: MongoClient, db_name: str = "dakb"):
        """
        Initialize DAKB collections manager.

        Args:
            mongo_client: MongoDB client instance
            db_name: Database name (default: dakb)
        """
        self.client = mongo_client
        self.db: Database = mongo_client[db_name]
        self._collections: dict[str, Collection] = {}

        # Initialize collection references
        for name in ALL_COLLECTIONS:
            self._collections[name] = self.db[name]

    @property
    def knowledge(self) -> Collection:
        """Get dakb_knowledge collection."""
        return self._collections[COLLECTION_KNOWLEDGE]

    @property
    def messages(self) -> Collection:
        """Get dakb_messages collection."""
        return self._collections[COLLECTION_MESSAGES]

    @property
    def agents(self) -> Collection:
        """Get dakb_agents collection."""
        return self._collections[COLLECTION_AGENTS]

    @property
    def sessions(self) -> Collection:
        """Get dakb_sessions collection."""
        return self._collections[COLLECTION_SESSIONS]

    @property
    def audit_log(self) -> Collection:
        """Get dakb_audit_log collection."""
        return self._collections[COLLECTION_AUDIT_LOG]

    @property
    def reputation(self) -> Collection:
        """Get dakb_agent_reputation collection."""
        return self._collections[COLLECTION_REPUTATION]

    @property
    def quality(self) -> Collection:
        """Get dakb_knowledge_quality collection."""
        return self._collections[COLLECTION_QUALITY]

    @property
    def flags(self) -> Collection:
        """Get dakb_knowledge_flags collection."""
        return self._collections[COLLECTION_FLAGS]

    @property
    def aliases(self) -> Collection:
        """Get dakb_agent_aliases collection."""
        return self._collections[COLLECTION_ALIASES]

    @property
    def invite_tokens(self) -> Collection:
        """Get dakb_invite_tokens collection (Self-Registration v1.0)."""
        return self._collections[COLLECTION_INVITE_TOKENS]

    @property
    def registration_audit(self) -> Collection:
        """Get dakb_registration_audit collection (Self-Registration v1.0)."""
        return self._collections[COLLECTION_REGISTRATION_AUDIT]

    def verify_collections(self) -> dict[str, bool]:
        """
        Verify all DAKB collections exist.

        Returns:
            Dictionary mapping collection names to existence status
        """
        existing = set(self.db.list_collection_names())
        return {name: name in existing for name in ALL_COLLECTIONS}

    def get_stats(self) -> dict[str, dict]:
        """
        Get statistics for all DAKB collections.

        Returns:
            Dictionary with document counts and storage stats per collection
        """
        stats = {}
        for name in ALL_COLLECTIONS:
            try:
                collection_stats = self.db.command("collStats", name)
                stats[name] = {
                    "count": collection_stats.get("count", 0),
                    "size_bytes": collection_stats.get("size", 0),
                    "avg_obj_size": collection_stats.get("avgObjSize", 0),
                    "storage_size": collection_stats.get("storageSize", 0),
                    "indexes": collection_stats.get("nindexes", 0),
                }
            except PyMongoError:
                # Collection might not exist yet
                stats[name] = {
                    "count": 0,
                    "size_bytes": 0,
                    "avg_obj_size": 0,
                    "storage_size": 0,
                    "indexes": 0,
                }
        return stats


class KnowledgeRepository:
    """
    Repository for dakb_knowledge collection CRUD operations.

    Handles knowledge storage, retrieval, updates, and voting.
    Coordinates with FAISS embedding service for semantic search.
    """

    def __init__(self, collection: Collection):
        """
        Initialize knowledge repository.

        Args:
            collection: MongoDB collection for dakb_knowledge
        """
        self.collection = collection

    def create(
        self,
        data: KnowledgeCreate,
        source: KnowledgeSource,
    ) -> DakbKnowledge:
        """
        Create a new knowledge entry.

        Args:
            data: Knowledge creation data
            source: Source information (agent, machine, session)

        Returns:
            Created knowledge entry

        Raises:
            DuplicateKeyError: If knowledge_id already exists
        """
        # Calculate expiration based on content type and explicit setting
        expires_at = None
        if data.expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=data.expires_in_days)

        knowledge = DakbKnowledge(
            title=data.title,
            content=data.content,
            content_type=data.content_type,
            format=data.format,
            code_language=data.code_language,
            category=data.category,
            tags=data.tags,
            source=source,
            access_level=data.access_level,
            related_files=data.related_files,
            confidence_score=data.confidence,
            expires_at=expires_at,
        )

        # Insert into MongoDB
        doc = knowledge.model_dump()
        self.collection.insert_one(doc)

        logger.info(f"Created knowledge: {knowledge.knowledge_id} - {knowledge.title}")
        return knowledge

    def get_by_id(self, knowledge_id: str) -> Optional[DakbKnowledge]:
        """
        Get knowledge entry by ID.

        Args:
            knowledge_id: Unique knowledge identifier

        Returns:
            Knowledge entry or None if not found
        """
        doc = self.collection.find_one({"knowledge_id": knowledge_id})
        if doc:
            doc.pop("_id", None)
            return DakbKnowledge(**doc)
        return None

    def get_by_ids(self, knowledge_ids: list[str]) -> list[DakbKnowledge]:
        """
        Get multiple knowledge entries by IDs.

        Args:
            knowledge_ids: List of knowledge identifiers

        Returns:
            List of knowledge entries (maintains order)
        """
        docs = list(self.collection.find({"knowledge_id": {"$in": knowledge_ids}}))

        # Create lookup map for ordering
        doc_map = {}
        for doc in docs:
            doc.pop("_id", None)
            doc_map[doc["knowledge_id"]] = DakbKnowledge(**doc)

        # Return in original order
        return [doc_map[kid] for kid in knowledge_ids if kid in doc_map]

    def update(
        self,
        knowledge_id: str,
        data: KnowledgeUpdate,
        updated_by: Optional[str] = None,
    ) -> Optional[DakbKnowledge]:
        """
        Update an existing knowledge entry.

        Args:
            knowledge_id: Knowledge identifier
            data: Update data
            updated_by: Agent performing the update

        Returns:
            Updated knowledge entry or None if not found
        """
        update_fields = data.model_dump(exclude_unset=True)
        if not update_fields:
            return self.get_by_id(knowledge_id)

        update_fields["updated_at"] = datetime.utcnow()

        # Increment version
        result = self.collection.find_one_and_update(
            {"knowledge_id": knowledge_id},
            {
                "$set": update_fields,
                "$inc": {"version": 1}
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(f"Updated knowledge: {knowledge_id}")
            return DakbKnowledge(**result)
        return None

    def delete(self, knowledge_id: str, soft: bool = True) -> bool:
        """
        Delete a knowledge entry.

        Args:
            knowledge_id: Knowledge identifier
            soft: If True, mark as deleted; if False, permanently remove

        Returns:
            True if deleted, False if not found
        """
        if soft:
            result = self.collection.update_one(
                {"knowledge_id": knowledge_id},
                {
                    "$set": {
                        "status": KnowledgeStatus.DELETED.value,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            success = result.modified_count > 0
        else:
            result = self.collection.delete_one({"knowledge_id": knowledge_id})
            success = result.deleted_count > 0

        if success:
            logger.info(f"Deleted knowledge: {knowledge_id} (soft={soft})")
        return success

    def vote(
        self,
        knowledge_id: str,
        agent_id: str,
        vote_data: VoteCreate,
    ) -> Optional[DakbKnowledge]:
        """
        Cast a vote on a knowledge entry.

        ISS-016 Fix: Prevents duplicate votes from the same agent.
        If an agent has already voted, their existing vote is updated
        rather than adding a new vote.

        Args:
            knowledge_id: Knowledge identifier
            agent_id: Voting agent
            vote_data: Vote information

        Returns:
            Updated knowledge entry or None if not found
        """
        # ISS-016 Fix: Check for existing vote from this agent
        existing = self.collection.find_one({
            "knowledge_id": knowledge_id,
            "vote_details.agent_id": agent_id
        })

        if existing:
            # Agent has already voted - update their existing vote
            old_vote = None
            for detail in existing.get("vote_details", []):
                if detail.get("agent_id") == agent_id:
                    old_vote = detail.get("vote")
                    break

            if old_vote:
                # Decrement old vote count
                old_vote_field = f"votes.{old_vote}"

                # Update the existing vote detail and adjust counts
                new_vote_field = f"votes.{vote_data.vote.value}"

                result = self.collection.find_one_and_update(
                    {
                        "knowledge_id": knowledge_id,
                        "vote_details.agent_id": agent_id
                    },
                    {
                        "$inc": {old_vote_field: -1, new_vote_field: 1},
                        "$set": {
                            "vote_details.$.vote": vote_data.vote.value,
                            "vote_details.$.comment": vote_data.comment,
                            "vote_details.$.used_successfully": vote_data.used_successfully,
                            "vote_details.$.voted_at": datetime.utcnow(),
                            "updated_at": datetime.utcnow()
                        }
                    },
                    return_document=True
                )

                if result:
                    result.pop("_id", None)
                    logger.info(
                        f"Vote updated on {knowledge_id}: {old_vote} -> {vote_data.vote.value} by {agent_id}"
                    )
                    return DakbKnowledge(**result)
                return None

        # New vote from this agent
        vote_detail = VoteDetail(
            agent_id=agent_id,
            vote=vote_data.vote,
            comment=vote_data.comment,
            used_successfully=vote_data.used_successfully,
        )

        # Map vote type to field
        vote_field = f"votes.{vote_data.vote.value}"

        result = self.collection.find_one_and_update(
            {"knowledge_id": knowledge_id},
            {
                "$inc": {vote_field: 1},
                "$push": {"vote_details": vote_detail.model_dump()},
                "$set": {"updated_at": datetime.utcnow()}
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(f"Vote recorded on {knowledge_id}: {vote_data.vote.value} by {agent_id}")
            return DakbKnowledge(**result)
        return None

    def record_access(
        self,
        knowledge_id: str,
        agent_id: str,
    ) -> bool:
        """
        Record access to knowledge entry for analytics.

        Args:
            knowledge_id: Knowledge identifier
            agent_id: Agent accessing the knowledge

        Returns:
            True if recorded successfully
        """
        result = self.collection.update_one(
            {"knowledge_id": knowledge_id},
            {
                "$inc": {"access_count": 1},
                "$set": {
                    "last_accessed_at": datetime.utcnow(),
                    "last_accessed_by": agent_id
                }
            }
        )
        return result.modified_count > 0

    def mark_indexed(self, knowledge_id: str, indexed: bool = True) -> bool:
        """
        Mark knowledge as indexed in FAISS.

        Args:
            knowledge_id: Knowledge identifier
            indexed: Whether the knowledge is indexed

        Returns:
            True if updated successfully
        """
        result = self.collection.update_one(
            {"knowledge_id": knowledge_id},
            {"$set": {"embedding_indexed": indexed}}
        )
        return result.modified_count > 0

    def find_by_category(
        self,
        category: str,
        access_level: Optional[AccessLevel] = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[DakbKnowledge]:
        """
        Find knowledge entries by category.

        Args:
            category: Category to filter by
            access_level: Optional access level filter
            limit: Maximum results
            skip: Number of results to skip

        Returns:
            List of matching knowledge entries
        """
        query: dict[str, Any] = {
            "category": category,
            "status": {"$nin": [KnowledgeStatus.DELETED.value, KnowledgeStatus.DEPRECATED.value]}
        }
        if access_level:
            query["access_level"] = access_level.value

        cursor = self.collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbKnowledge(**doc))
        return results

    def find_by_tags(
        self,
        tags: list[str],
        match_all: bool = False,
        limit: int = 100,
    ) -> list[DakbKnowledge]:
        """
        Find knowledge entries by tags.

        Args:
            tags: Tags to search for
            match_all: If True, require all tags; if False, any tag
            limit: Maximum results

        Returns:
            List of matching knowledge entries
        """
        if match_all:
            query = {"tags": {"$all": tags}}
        else:
            query = {"tags": {"$in": tags}}

        query["status"] = {"$nin": [KnowledgeStatus.DELETED.value]}

        cursor = self.collection.find(query).sort("votes.helpful", -1).limit(limit)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbKnowledge(**doc))
        return results

    def find_expired(self, before: Optional[datetime] = None) -> list[str]:
        """
        Find expired knowledge IDs.

        Args:
            before: Consider expired if expires_at is before this time

        Returns:
            List of expired knowledge IDs
        """
        if before is None:
            before = datetime.utcnow()

        cursor = self.collection.find(
            {
                "expires_at": {"$lt": before, "$ne": None}
            },
            {"knowledge_id": 1}
        )
        return [doc["knowledge_id"] for doc in cursor]

    def find_active_for_indexing(self) -> list[dict]:
        """
        Find all active knowledge for FAISS index rebuild.

        Returns:
            List of documents with knowledge_id, title, content
        """
        cursor = self.collection.find(
            {
                "embedding_indexed": True,
                "status": {"$nin": [KnowledgeStatus.DEPRECATED.value, KnowledgeStatus.DELETED.value]},
                "$or": [
                    {"expires_at": None},
                    {"expires_at": {"$gt": datetime.utcnow()}}
                ]
            },
            {"knowledge_id": 1, "title": 1, "content": 1}
        )
        return list(cursor)

    def get_statistics(self) -> dict:
        """
        Get knowledge base statistics using MongoDB aggregation.

        ISS-039 Fix: Added missing method required by /stats endpoint.

        Returns:
            Dictionary with statistics:
            - total_entries: Total count of non-deleted entries
            - by_category: Counts grouped by category
            - by_content_type: Counts grouped by content type
            - by_access_level: Counts grouped by access level
            - top_tags: Most frequently used tags (top 20)
            - indexed_count: Number of entries with embedding_indexed=True
            - expired_count: Number of expired entries
        """
        now = datetime.utcnow()

        # Base filter: exclude deleted entries
        base_filter = {
            "status": {"$ne": KnowledgeStatus.DELETED.value}
        }

        # Total entries count
        total_entries = self.collection.count_documents(base_filter)

        # Count by category using aggregation
        category_pipeline = [
            {"$match": base_filter},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}}
        ]
        by_category = {}
        for doc in self.collection.aggregate(category_pipeline):
            if doc["_id"]:
                by_category[doc["_id"]] = doc["count"]

        # Count by content_type using aggregation
        content_type_pipeline = [
            {"$match": base_filter},
            {"$group": {"_id": "$content_type", "count": {"$sum": 1}}}
        ]
        by_content_type = {}
        for doc in self.collection.aggregate(content_type_pipeline):
            if doc["_id"]:
                by_content_type[doc["_id"]] = doc["count"]

        # Count by access_level using aggregation
        access_level_pipeline = [
            {"$match": base_filter},
            {"$group": {"_id": "$access_level", "count": {"$sum": 1}}}
        ]
        by_access_level = {}
        for doc in self.collection.aggregate(access_level_pipeline):
            if doc["_id"]:
                by_access_level[doc["_id"]] = doc["count"]

        # Top tags using aggregation (unwind tags array, group, sort, limit)
        tags_pipeline = [
            {"$match": base_filter},
            {"$unwind": "$tags"},
            {"$group": {"_id": "$tags", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 20}
        ]
        top_tags = []
        for doc in self.collection.aggregate(tags_pipeline):
            if doc["_id"]:
                top_tags.append({"tag": doc["_id"], "count": doc["count"]})

        # Count indexed entries
        indexed_count = self.collection.count_documents({
            **base_filter,
            "embedding_indexed": True
        })

        # Count expired entries
        expired_count = self.collection.count_documents({
            **base_filter,
            "expires_at": {"$lt": now, "$ne": None}
        })

        return {
            "total_entries": total_entries,
            "by_category": by_category,
            "by_content_type": by_content_type,
            "by_access_level": by_access_level,
            "top_tags": top_tags,
            "indexed_count": indexed_count,
            "expired_count": expired_count
        }


class MessageRepository:
    """
    Repository for dakb_messages collection CRUD operations.

    Handles message sending, retrieval, and status updates.
    """

    def __init__(self, collection: Collection):
        """Initialize message repository."""
        self.collection = collection

    def send(
        self,
        data: MessageCreate,
        from_agent: str,
        from_machine: str,
    ) -> DakbMessage:
        """
        Send a new message.

        Args:
            data: Message creation data
            from_agent: Sender agent ID
            from_machine: Sender machine ID

        Returns:
            Created message
        """
        expires_at = datetime.utcnow() + timedelta(days=data.expires_in_days)

        message = DakbMessage(
            from_agent=from_agent,
            from_machine=from_machine,
            to_agent=data.to_agent,
            to_topic=data.to_topic,
            message_type=data.message_type,
            priority=data.priority,
            subject=data.subject,
            body=data.body,
            attachments=data.attachments,
            thread_id=data.thread_id,
            reply_to=data.reply_to,
            expires_at=expires_at,
        )

        doc = message.model_dump()
        self.collection.insert_one(doc)

        logger.info(f"Message sent: {message.message_id} from {from_agent} to {data.to_agent or data.to_topic}")
        return message

    def get_by_id(self, message_id: str) -> Optional[DakbMessage]:
        """Get message by ID."""
        doc = self.collection.find_one({"message_id": message_id})
        if doc:
            doc.pop("_id", None)
            return DakbMessage(**doc)
        return None

    def get_inbox(
        self,
        agent_id: str,
        status: Optional[MessageStatus] = None,
        limit: int = 50,
    ) -> list[DakbMessage]:
        """
        Get messages for an agent.

        Args:
            agent_id: Target agent ID
            status: Optional status filter
            limit: Maximum results

        Returns:
            List of messages
        """
        query: dict[str, Any] = {"to_agent": agent_id}
        if status:
            query["status"] = status.value

        cursor = self.collection.find(query).sort([
            ("priority", -1),  # Urgent first
            ("created_at", -1)
        ]).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbMessage(**doc))
        return results

    def get_by_topic(
        self,
        topic: str,
        since: Optional[datetime] = None,
        limit: int = 50,
    ) -> list[DakbMessage]:
        """
        Get messages for a topic.

        Args:
            topic: Topic name
            since: Only get messages after this time
            limit: Maximum results

        Returns:
            List of messages
        """
        query: dict[str, Any] = {"to_topic": topic}
        if since:
            query["created_at"] = {"$gt": since}

        cursor = self.collection.find(query).sort("created_at", -1).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbMessage(**doc))
        return results

    def mark_delivered(self, message_id: str) -> bool:
        """Mark message as delivered."""
        result = self.collection.update_one(
            {"message_id": message_id},
            {
                "$set": {
                    "status": MessageStatus.DELIVERED.value,
                    "delivered_at": datetime.utcnow()
                }
            }
        )
        return result.modified_count > 0

    def mark_read(self, message_id: str, agent_id: str) -> bool:
        """Mark message as read by an agent."""
        result = self.collection.update_one(
            {"message_id": message_id},
            {
                "$set": {
                    "status": MessageStatus.READ.value,
                    "read_at": datetime.utcnow()
                },
                "$addToSet": {"read_by": agent_id}
            }
        )
        return result.modified_count > 0

    def count_unread(self, agent_id: str) -> int:
        """Count unread messages for an agent."""
        return self.collection.count_documents({
            "to_agent": agent_id,
            "status": {"$in": [MessageStatus.PENDING.value, MessageStatus.DELIVERED.value]}
        })


class AgentRepository:
    """
    Repository for dakb_agents collection CRUD operations.

    Handles agent registration, updates, and status management.
    """

    def __init__(self, collection: Collection):
        """Initialize agent repository."""
        self.collection = collection

    def register(self, data: AgentRegister) -> DakbAgent:
        """
        Register a new agent.

        Args:
            data: Agent registration data

        Returns:
            Registered agent

        Raises:
            DuplicateKeyError: If agent_id already exists
        """
        agent = DakbAgent(
            agent_id=data.agent_id,
            display_name=data.display_name,
            agent_type=data.agent_type,
            model_version=data.model_version,
            machine_id=data.machine_id,
            machine_name=data.machine_name,
            capabilities=data.capabilities,
            specializations=data.specializations,
            status=AgentStatus.ACTIVE,
            last_seen=datetime.utcnow(),
        )

        doc = agent.model_dump()
        self.collection.insert_one(doc)

        logger.info(f"Agent registered: {agent.agent_id} ({agent.agent_type.value})")
        return agent

    def get_by_id(self, agent_id: str) -> Optional[DakbAgent]:
        """Get agent by ID."""
        doc = self.collection.find_one({"agent_id": agent_id})
        if doc:
            doc.pop("_id", None)
            return DakbAgent(**doc)
        return None

    def update(self, agent_id: str, data: AgentUpdate) -> Optional[DakbAgent]:
        """Update agent information."""
        update_fields = data.model_dump(exclude_unset=True)
        if not update_fields:
            return self.get_by_id(agent_id)

        update_fields["updated_at"] = datetime.utcnow()

        result = self.collection.find_one_and_update(
            {"agent_id": agent_id},
            {"$set": update_fields},
            return_document=True
        )

        if result:
            result.pop("_id", None)
            return DakbAgent(**result)
        return None

    def heartbeat(self, agent_id: str, activity: Optional[str] = None) -> bool:
        """
        Update agent heartbeat (last_seen).

        Args:
            agent_id: Agent identifier
            activity: Optional description of current activity

        Returns:
            True if updated successfully
        """
        update = {
            "status": AgentStatus.ACTIVE.value,
            "last_seen": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        if activity:
            update["last_activity"] = activity

        result = self.collection.update_one(
            {"agent_id": agent_id},
            {"$set": update}
        )
        return result.modified_count > 0

    def set_offline(self, agent_id: str) -> bool:
        """Mark agent as offline."""
        result = self.collection.update_one(
            {"agent_id": agent_id},
            {
                "$set": {
                    "status": AgentStatus.OFFLINE.value,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        return result.modified_count > 0

    def find_active(self, since_minutes: int = 5) -> list[DakbAgent]:
        """Find agents active within the specified time."""
        since = datetime.utcnow() - timedelta(minutes=since_minutes)
        cursor = self.collection.find({
            "status": AgentStatus.ACTIVE.value,
            "last_seen": {"$gte": since}
        })

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbAgent(**doc))
        return results

    def find_by_capability(self, capability: str) -> list[DakbAgent]:
        """Find agents with a specific capability."""
        cursor = self.collection.find({
            "capabilities": capability,
            "status": {"$ne": AgentStatus.SUSPENDED.value}
        })

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbAgent(**doc))
        return results

    def increment_stats(
        self,
        agent_id: str,
        knowledge_contributed: int = 0,
        messages_sent: int = 0,
        messages_received: int = 0,
    ) -> bool:
        """Increment agent statistics."""
        inc = {}
        if knowledge_contributed:
            inc["knowledge_contributed"] = knowledge_contributed
        if messages_sent:
            inc["messages_sent"] = messages_sent
        if messages_received:
            inc["messages_received"] = messages_received

        if not inc:
            return True

        result = self.collection.update_one(
            {"agent_id": agent_id},
            {"$inc": inc}
        )
        return result.modified_count > 0


class SessionRepository:
    """
    Repository for dakb_sessions collection CRUD operations.

    Handles session creation, updates, and handoff operations.
    """

    def __init__(self, collection: Collection):
        """Initialize session repository."""
        self.collection = collection

    def create(
        self,
        agent_id: str,
        machine_id: str,
        data: Optional[SessionCreate] = None,
    ) -> DakbSession:
        """
        Create a new session.

        Args:
            agent_id: Agent ID
            machine_id: Machine ID
            data: Optional session creation data

        Returns:
            Created session
        """
        session = DakbSession(
            agent_id=agent_id,
            machine_id=machine_id,
            task_description=data.task_description if data else None,
            loaded_contexts=data.loaded_contexts if data else [],
            working_files=data.working_files if data else [],
        )

        doc = session.model_dump()
        self.collection.insert_one(doc)

        logger.info(f"Session created: {session.session_id} for {agent_id}")
        return session

    def get_by_id(self, session_id: str) -> Optional[DakbSession]:
        """Get session by ID."""
        doc = self.collection.find_one({"session_id": session_id})
        if doc:
            doc.pop("_id", None)
            return DakbSession(**doc)
        return None

    def get_active_for_agent(self, agent_id: str) -> Optional[DakbSession]:
        """Get active session for an agent."""
        doc = self.collection.find_one({
            "agent_id": agent_id,
            "ended_at": None,
            "task_status": {"$in": [TaskStatus.IN_PROGRESS.value, TaskStatus.PAUSED.value]}
        })
        if doc:
            doc.pop("_id", None)
            return DakbSession(**doc)
        return None

    def update(self, session_id: str, data: SessionUpdate) -> Optional[DakbSession]:
        """Update session information."""
        update_fields = data.model_dump(exclude_unset=True)
        if not update_fields:
            return self.get_by_id(session_id)

        update_fields["last_activity"] = datetime.utcnow()

        result = self.collection.find_one_and_update(
            {"session_id": session_id},
            {"$set": update_fields},
            return_document=True
        )

        if result:
            result.pop("_id", None)
            return DakbSession(**result)
        return None

    def add_knowledge(self, session_id: str, knowledge_id: str) -> bool:
        """Add knowledge ID to session."""
        result = self.collection.update_one(
            {"session_id": session_id},
            {
                "$addToSet": {"knowledge_ids": knowledge_id},
                "$set": {"last_activity": datetime.utcnow()}
            }
        )
        return result.modified_count > 0

    def end_session(self, session_id: str) -> bool:
        """End a session."""
        result = self.collection.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "ended_at": datetime.utcnow(),
                    "task_status": TaskStatus.COMPLETED.value,
                    "last_activity": datetime.utcnow()
                }
            }
        )
        if result.modified_count > 0:
            logger.info(f"Session ended: {session_id}")
            return True
        return False

    def handoff(
        self,
        session_id: str,
        to_agent: str,
        notes: Optional[str] = None,
    ) -> bool:
        """
        Hand off a session to another agent.

        Args:
            session_id: Session to hand off
            to_agent: Target agent ID
            notes: Optional handoff notes

        Returns:
            True if handoff successful
        """
        result = self.collection.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "handed_off_to": to_agent,
                    "handoff_notes": notes,
                    "handoff_timestamp": datetime.utcnow(),
                    "task_status": TaskStatus.HANDED_OFF.value,
                    "last_activity": datetime.utcnow()
                }
            }
        )
        if result.modified_count > 0:
            logger.info(f"Session {session_id} handed off to {to_agent}")
            return True
        return False


class AuditRepository:
    """
    Repository for dakb_audit_log collection operations.

    Handles audit logging for security and compliance.
    """

    def __init__(self, collection: Collection):
        """Initialize audit repository."""
        self.collection = collection

    def log(
        self,
        agent_id: str,
        action: AuditAction,
        resource_type: ResourceType,
        resource_id: str,
        details: Optional[dict] = None,
        machine_id: Optional[str] = None,
        session_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        access_level_required: Optional[AccessLevel] = None,
        access_granted: bool = True,
        denial_reason: Optional[str] = None,
    ) -> DakbAuditLog:
        """
        Create an audit log entry.

        Args:
            agent_id: Agent performing the action
            action: Action type
            resource_type: Type of resource affected
            resource_id: ID of affected resource
            details: Additional details
            machine_id: Machine ID
            session_id: Session ID
            ip_address: IP address
            access_level_required: Required access level
            access_granted: Whether access was granted
            denial_reason: Reason if access denied

        Returns:
            Created audit log entry
        """
        log_entry = DakbAuditLog(
            agent_id=agent_id,
            machine_id=machine_id,
            session_id=session_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
            access_level_required=access_level_required,
            access_granted=access_granted,
            denial_reason=denial_reason,
        )

        doc = log_entry.model_dump()
        self.collection.insert_one(doc)

        if not access_granted:
            logger.warning(
                f"Access denied: {agent_id} -> {action.value} on {resource_type.value}/{resource_id}"
            )

        return log_entry

    def find_by_agent(
        self,
        agent_id: str,
        action: Optional[AuditAction] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[DakbAuditLog]:
        """Find audit logs by agent."""
        query: dict[str, Any] = {"agent_id": agent_id}
        if action:
            query["action"] = action.value
        if since:
            query["timestamp"] = {"$gte": since}

        cursor = self.collection.find(query).sort("timestamp", -1).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbAuditLog(**doc))
        return results

    def find_by_resource(
        self,
        resource_type: ResourceType,
        resource_id: str,
        limit: int = 100,
    ) -> list[DakbAuditLog]:
        """Find audit logs for a specific resource."""
        cursor = self.collection.find({
            "resource_type": resource_type.value,
            "resource_id": resource_id
        }).sort("timestamp", -1).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbAuditLog(**doc))
        return results

    def find_access_denials(
        self,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[DakbAuditLog]:
        """Find access denial events for security monitoring."""
        query: dict[str, Any] = {"access_granted": False}
        if since:
            query["timestamp"] = {"$gte": since}

        cursor = self.collection.find(query).sort("timestamp", -1).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbAuditLog(**doc))
        return results

    def count_actions(
        self,
        agent_id: Optional[str] = None,
        action: Optional[AuditAction] = None,
        since: Optional[datetime] = None,
    ) -> int:
        """Count audit log entries matching criteria."""
        query: dict[str, Any] = {}
        if agent_id:
            query["agent_id"] = agent_id
        if action:
            query["action"] = action.value
        if since:
            query["timestamp"] = {"$gte": since}

        return self.collection.count_documents(query)


# =============================================================================
# REPUTATION REPOSITORY (Step 2.3)
# =============================================================================

class ReputationRepository:
    """
    Repository for agent reputation management.

    Implements reputation scoring, vote weighting, and leaderboard functionality.

    Formulas:
    - Reputation: rep = (knowledge_count * 10) + (helpful_received * 5) - (unhelpful_received * 2)
    - Vote Weight: weight = 1 + (reputation / 1000), capped at 3x
    """

    def __init__(self, collection: Collection):
        """Initialize reputation repository."""
        self.collection = collection

    def get_or_create(self, agent_id: str) -> AgentReputation:
        """
        Get or create reputation record for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            AgentReputation record
        """
        doc = self.collection.find_one({"agent_id": agent_id})
        if doc:
            doc.pop("_id", None)
            return AgentReputation(**doc)

        # Create new reputation record
        reputation = AgentReputation(agent_id=agent_id)
        self.collection.insert_one(reputation.model_dump())
        logger.info(f"Created reputation record for agent: {agent_id}")
        return reputation

    def get_by_id(self, agent_id: str) -> Optional[AgentReputation]:
        """Get reputation by agent ID."""
        doc = self.collection.find_one({"agent_id": agent_id})
        if doc:
            doc.pop("_id", None)
            return AgentReputation(**doc)
        return None

    def update_on_knowledge_created(self, agent_id: str, knowledge_id: str) -> AgentReputation:
        """
        Update reputation when agent creates knowledge.

        Args:
            agent_id: Agent who created knowledge
            knowledge_id: Created knowledge ID

        Returns:
            Updated reputation
        """
        now = datetime.utcnow()

        # Get current reputation for history
        current = self.get_or_create(agent_id)
        old_score = current.reputation_score

        # Update counters and recalculate
        result = self.collection.find_one_and_update(
            {"agent_id": agent_id},
            {
                "$inc": {"knowledge_contributed": 1},
                "$set": {
                    "updated_at": now,
                    "last_contribution_at": now
                },
                "$setOnInsert": {"first_contribution_at": now}
            },
            upsert=True,
            return_document=True
        )

        if result:
            result.pop("_id", None)
            reputation = AgentReputation(**result)

            # Calculate new score
            new_score = reputation.calculate_reputation()

            # Add history entry
            history_entry = ReputationHistory(
                previous_score=old_score,
                new_score=new_score,
                change_reason="knowledge_created",
                change_source=knowledge_id
            )

            # Update with new score and history
            self.collection.update_one(
                {"agent_id": agent_id},
                {
                    "$set": {"reputation_score": new_score},
                    "$push": {
                        "reputation_history": {
                            "$each": [history_entry.model_dump()],
                            "$slice": -100  # Keep last 100 entries
                        }
                    }
                }
            )

            reputation.reputation_score = new_score
            logger.info(f"Reputation updated for {agent_id}: {old_score} -> {new_score} (knowledge created)")
            return reputation

        return current

    def update_on_vote_received(
        self,
        agent_id: str,
        vote_type: VoteType,
        knowledge_id: str
    ) -> AgentReputation:
        """
        Update reputation when agent's knowledge receives a vote.

        Args:
            agent_id: Agent who owns the knowledge
            vote_type: Type of vote received
            knowledge_id: Knowledge that received the vote

        Returns:
            Updated reputation
        """
        current = self.get_or_create(agent_id)
        old_score = current.reputation_score

        # Map vote type to field
        vote_field_map = {
            VoteType.HELPFUL: "helpful_votes_received",
            VoteType.UNHELPFUL: "unhelpful_votes_received",
            VoteType.OUTDATED: "outdated_votes_received",
            VoteType.INCORRECT: "incorrect_votes_received",
        }

        field = vote_field_map.get(vote_type)
        if not field:
            return current

        # Increment vote counter
        result = self.collection.find_one_and_update(
            {"agent_id": agent_id},
            {
                "$inc": {field: 1},
                "$set": {"updated_at": datetime.utcnow()}
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            reputation = AgentReputation(**result)

            # Calculate new score
            new_score = reputation.calculate_reputation()

            # Calculate accuracy and helpfulness rates
            total_votes = (
                reputation.helpful_votes_received
                + reputation.unhelpful_votes_received
                + reputation.outdated_votes_received
                + reputation.incorrect_votes_received
            )

            accuracy_rate = 1.0
            helpfulness_rate = 0.0
            if total_votes > 0:
                accuracy_rate = 1.0 - (reputation.incorrect_votes_received / total_votes)
                helpfulness_rate = reputation.helpful_votes_received / total_votes

            # Add history entry
            history_entry = ReputationHistory(
                previous_score=old_score,
                new_score=new_score,
                change_reason=f"vote_received_{vote_type.value}",
                change_source=knowledge_id
            )

            # Update with new score, rates, and history
            self.collection.update_one(
                {"agent_id": agent_id},
                {
                    "$set": {
                        "reputation_score": new_score,
                        "accuracy_rate": accuracy_rate,
                        "helpfulness_rate": helpfulness_rate
                    },
                    "$push": {
                        "reputation_history": {
                            "$each": [history_entry.model_dump()],
                            "$slice": -100
                        }
                    }
                }
            )

            reputation.reputation_score = new_score
            reputation.accuracy_rate = accuracy_rate
            reputation.helpfulness_rate = helpfulness_rate
            logger.info(
                f"Reputation updated for {agent_id}: {old_score} -> {new_score} "
                f"(received {vote_type.value} vote)"
            )
            return reputation

        return current

    def update_on_vote_cast(
        self,
        agent_id: str,
        vote_type: VoteType
    ) -> AgentReputation:
        """
        Update reputation when agent casts a vote.

        Args:
            agent_id: Agent who cast the vote
            vote_type: Type of vote cast

        Returns:
            Updated reputation
        """
        inc_fields: dict[str, int] = {"votes_cast": 1}
        if vote_type == VoteType.HELPFUL:
            inc_fields["votes_cast_helpful"] = 1
        elif vote_type == VoteType.UNHELPFUL:
            inc_fields["votes_cast_unhelpful"] = 1

        result = self.collection.find_one_and_update(
            {"agent_id": agent_id},
            {
                "$inc": inc_fields,
                "$set": {"updated_at": datetime.utcnow()}
            },
            upsert=True,
            return_document=True
        )

        if result:
            result.pop("_id", None)
            return AgentReputation(**result)

        return self.get_or_create(agent_id)

    def get_vote_weight(self, agent_id: str) -> float:
        """
        Get vote weight for an agent based on their reputation.

        Formula: weight = 1 + (reputation / 1000), capped at 3x

        Args:
            agent_id: Agent identifier

        Returns:
            Vote weight multiplier (1.0 to 3.0)
        """
        reputation = self.get_or_create(agent_id)
        return reputation.calculate_vote_weight()

    def get_leaderboard(
        self,
        metric: str = "reputation",
        limit: int = 10
    ) -> list[LeaderboardEntry]:
        """
        Get agent leaderboard by specified metric.

        Args:
            metric: Metric to rank by (reputation, contributions, helpfulness)
            limit: Maximum entries to return

        Returns:
            List of leaderboard entries
        """
        # Map metric to field
        field_map = {
            "reputation": "reputation_score",
            "contributions": "knowledge_contributed",
            "helpfulness": "helpful_votes_received",
        }

        sort_field = field_map.get(metric, "reputation_score")

        cursor = self.collection.find(
            {},
            {"agent_id": 1, sort_field: 1}
        ).sort(sort_field, -1).limit(limit)

        entries = []
        for rank, doc in enumerate(cursor, 1):
            entries.append(LeaderboardEntry(
                rank=rank,
                agent_id=doc["agent_id"],
                score=doc.get(sort_field, 0),
                metric=metric
            ))

        return entries

    def get_agent_rank(self, agent_id: str, metric: str = "reputation") -> Optional[int]:
        """
        Get an agent's rank for a specific metric.

        Args:
            agent_id: Agent identifier
            metric: Metric to rank by

        Returns:
            Rank (1-indexed) or None if not found
        """
        field_map = {
            "reputation": "reputation_score",
            "contributions": "knowledge_contributed",
            "helpfulness": "helpful_votes_received",
        }

        sort_field = field_map.get(metric, "reputation_score")

        # Get agent's score
        agent = self.get_by_id(agent_id)
        if not agent:
            return None

        agent_score = getattr(agent, sort_field.replace("_score", ""), 0)
        if sort_field == "reputation_score":
            agent_score = agent.reputation_score

        # Count agents with higher scores
        higher_count = self.collection.count_documents({
            sort_field: {"$gt": agent_score}
        })

        return higher_count + 1


# =============================================================================
# QUALITY REPOSITORY (Step 2.3)
# =============================================================================

class QualityRepository:
    """
    Repository for knowledge quality scoring.

    Implements quality calculation based on weighted votes.

    ISS-046 Fix: Formula uses raw counts for outdated/incorrect penalties.
    Formula: quality = (weighted_helpful - weighted_unhelpful + (outdated_count * -3) + (incorrect_count * -5)) / total_votes
    Auto-deprecation threshold: quality < -0.5

    ISS-047 Fix: Tracks voter_agents to prevent duplicate votes.
    """

    def __init__(self, collection: Collection):
        """Initialize quality repository."""
        self.collection = collection

    def get_or_create(self, knowledge_id: str) -> KnowledgeQuality:
        """
        Get or create quality record for knowledge.

        Args:
            knowledge_id: Knowledge identifier

        Returns:
            KnowledgeQuality record
        """
        doc = self.collection.find_one({"knowledge_id": knowledge_id})
        if doc:
            doc.pop("_id", None)
            # Remove voter_agents from response (internal tracking only)
            doc.pop("voter_agents", None)
            return KnowledgeQuality(**doc)

        # Create new quality record with empty voter_agents array
        quality = KnowledgeQuality(knowledge_id=knowledge_id)
        quality_doc = quality.model_dump()
        quality_doc["voter_agents"] = []  # ISS-047: Track who has voted
        self.collection.insert_one(quality_doc)
        logger.info(f"Created quality record for knowledge: {knowledge_id}")
        return quality

    def get_by_id(self, knowledge_id: str) -> Optional[KnowledgeQuality]:
        """Get quality by knowledge ID."""
        doc = self.collection.find_one({"knowledge_id": knowledge_id})
        if doc:
            doc.pop("_id", None)
            doc.pop("voter_agents", None)  # Remove internal tracking field
            return KnowledgeQuality(**doc)
        return None

    def has_voted(self, knowledge_id: str, agent_id: str) -> bool:
        """
        Check if an agent has already voted on this knowledge.

        ISS-047: Duplicate vote prevention helper.

        Args:
            knowledge_id: Knowledge identifier
            agent_id: Agent identifier

        Returns:
            True if agent has already voted
        """
        doc = self.collection.find_one({
            "knowledge_id": knowledge_id,
            "voter_agents": agent_id
        })
        return doc is not None

    def record_vote(
        self,
        knowledge_id: str,
        vote_type: VoteType,
        vote_weight: float = 1.0,
        agent_id: Optional[str] = None
    ) -> KnowledgeQuality:
        """
        Record a weighted vote on knowledge quality.

        ISS-047 Fix: Checks for duplicate votes if agent_id is provided.
        ISS-046 Fix: Increments raw counts for outdated/incorrect.

        Args:
            knowledge_id: Knowledge identifier
            vote_type: Type of vote
            vote_weight: Weight of the vote (based on voter reputation)
            agent_id: Optional agent ID for duplicate prevention

        Returns:
            Updated quality record
        """
        # ISS-047: Check for duplicate vote if agent_id provided
        if agent_id:
            if self.has_voted(knowledge_id, agent_id):
                logger.warning(
                    f"Duplicate vote prevented: {agent_id} already voted on {knowledge_id}"
                )
                # Return current quality without recording duplicate
                return self.get_or_create(knowledge_id)

        # Map vote type to weighted field
        field_map = {
            VoteType.HELPFUL: "weighted_helpful",
            VoteType.UNHELPFUL: "weighted_unhelpful",
            VoteType.OUTDATED: "weighted_outdated",
            VoteType.INCORRECT: "weighted_incorrect",
        }

        field = field_map.get(vote_type)
        if not field:
            return self.get_or_create(knowledge_id)

        # Build increment operations
        inc_ops: dict[str, Any] = {
            field: vote_weight,
            "total_votes": 1
        }

        # ISS-046: Track raw counts for outdated/incorrect (used in formula)
        if vote_type == VoteType.OUTDATED:
            inc_ops["outdated_count"] = 1
        elif vote_type == VoteType.INCORRECT:
            inc_ops["incorrect_count"] = 1

        # Build update operations
        update_ops: dict[str, Any] = {
            "$inc": inc_ops,
            "$set": {"updated_at": datetime.utcnow()}
        }

        # ISS-047: Add agent to voter_agents array if provided
        if agent_id:
            update_ops["$addToSet"] = {"voter_agents": agent_id}

        # Update vote counts
        result = self.collection.find_one_and_update(
            {"knowledge_id": knowledge_id},
            update_ops,
            upsert=True,
            return_document=True
        )

        if result:
            result.pop("_id", None)
            result.pop("voter_agents", None)  # Remove internal tracking field
            quality = KnowledgeQuality(**result)

            # Calculate new quality score
            new_score = quality.calculate_quality()
            should_deprecate = quality.should_auto_deprecate()

            # Update quality score and deprecation warning
            self.collection.update_one(
                {"knowledge_id": knowledge_id},
                {
                    "$set": {
                        "quality_score": new_score,
                        "auto_deprecation_warning": should_deprecate
                    }
                }
            )

            quality.quality_score = new_score
            quality.auto_deprecation_warning = should_deprecate

            if should_deprecate:
                logger.warning(
                    f"Knowledge {knowledge_id} flagged for auto-deprecation "
                    f"(quality score: {new_score:.2f})"
                )

            return quality

        return self.get_or_create(knowledge_id)

    def record_usage(self, knowledge_id: str) -> bool:
        """
        Record successful usage of knowledge.

        Args:
            knowledge_id: Knowledge identifier

        Returns:
            True if updated successfully
        """
        result = self.collection.update_one(
            {"knowledge_id": knowledge_id},
            {
                "$inc": {"usage_count": 1},
                "$set": {"updated_at": datetime.utcnow()}
            }
        )
        return result.modified_count > 0

    def get_low_quality(self, threshold: float = -0.5, limit: int = 50) -> list[KnowledgeQuality]:
        """
        Get knowledge entries below quality threshold.

        Args:
            threshold: Quality score threshold
            limit: Maximum entries to return

        Returns:
            List of low-quality knowledge records
        """
        cursor = self.collection.find(
            {"quality_score": {"$lt": threshold}}
        ).sort("quality_score", 1).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(KnowledgeQuality(**doc))
        return results

    def get_flagged_for_deprecation(self) -> list[str]:
        """
        Get knowledge IDs flagged for auto-deprecation.

        Returns:
            List of knowledge IDs
        """
        cursor = self.collection.find(
            {"auto_deprecation_warning": True},
            {"knowledge_id": 1}
        )
        return [doc["knowledge_id"] for doc in cursor]


# =============================================================================
# FLAG REPOSITORY (Step 2.3)
# =============================================================================

class FlagRepository:
    """
    Repository for knowledge flags and moderation.

    Handles flagging knowledge for review and moderation actions.
    """

    def __init__(self, collection: Collection):
        """Initialize flag repository."""
        self.collection = collection

    def create_flag(
        self,
        knowledge_id: str,
        flagged_by: str,
        reason: FlagReason,
        details: Optional[str] = None
    ) -> KnowledgeFlag:
        """
        Flag knowledge for review.

        Args:
            knowledge_id: Knowledge to flag
            flagged_by: Agent flagging
            reason: Reason for flagging
            details: Additional details

        Returns:
            Created flag
        """
        flag = KnowledgeFlag(
            knowledge_id=knowledge_id,
            flagged_by=flagged_by,
            reason=reason,
            details=details
        )

        self.collection.insert_one(flag.model_dump())
        logger.info(f"Knowledge {knowledge_id} flagged for {reason.value} by {flagged_by}")
        return flag

    def get_by_id(self, flag_id: str) -> Optional[KnowledgeFlag]:
        """Get flag by ID."""
        doc = self.collection.find_one({"flag_id": flag_id})
        if doc:
            doc.pop("_id", None)
            return KnowledgeFlag(**doc)
        return None

    def get_pending_flags(self, limit: int = 50) -> list[KnowledgeFlag]:
        """
        Get pending flags awaiting review.

        Args:
            limit: Maximum flags to return

        Returns:
            List of pending flags
        """
        cursor = self.collection.find(
            {"status": "pending"}
        ).sort("created_at", 1).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(KnowledgeFlag(**doc))
        return results

    def get_flags_for_knowledge(self, knowledge_id: str) -> list[KnowledgeFlag]:
        """
        Get all flags for a knowledge entry.

        Args:
            knowledge_id: Knowledge identifier

        Returns:
            List of flags
        """
        cursor = self.collection.find(
            {"knowledge_id": knowledge_id}
        ).sort("created_at", -1)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(KnowledgeFlag(**doc))
        return results

    def resolve_flag(
        self,
        flag_id: str,
        reviewed_by: str,
        resolution: str
    ) -> Optional[KnowledgeFlag]:
        """
        Resolve a flag.

        Args:
            flag_id: Flag to resolve
            reviewed_by: Moderator agent
            resolution: Resolution description

        Returns:
            Updated flag or None
        """
        result = self.collection.find_one_and_update(
            {"flag_id": flag_id},
            {
                "$set": {
                    "status": "resolved",
                    "reviewed_by": reviewed_by,
                    "reviewed_at": datetime.utcnow(),
                    "resolution": resolution
                }
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(f"Flag {flag_id} resolved by {reviewed_by}: {resolution}")
            return KnowledgeFlag(**result)
        return None

    def count_pending(self) -> int:
        """Count pending flags."""
        return self.collection.count_documents({"status": "pending"})


# =============================================================================
# ALIAS REPOSITORY (Token Team with Agent Aliases)
# =============================================================================

class AliasRepository:
    """
    Repository for agent alias management (Token Team system).

    Allows one token to register multiple aliases for message routing.
    Messages to any alias route to the token owner's shared inbox.

    Key features:
    - Global alias uniqueness enforcement
    - Alias-to-token resolution for message routing (supports both alias AND agent_id)
    - Token ownership validation for alias management
    - Active/inactive alias lifecycle management
    """

    def __init__(self, collection: Collection, agents_collection: Optional[Collection] = None):
        """
        Initialize alias repository.

        Args:
            collection: The dakb_agent_aliases collection
            agents_collection: Optional dakb_agents collection for direct agent_id routing
        """
        self.collection = collection
        self.agents_collection = agents_collection

    def register_alias(
        self,
        token_id: str,
        alias: str,
        role: Optional[str] = None,
        metadata: Optional[dict] = None
    ) -> DakbAgentAlias:
        """
        Register a new alias for a token.

        The alias must be globally unique across all tokens.
        Only the owning token can register aliases for itself.

        Args:
            token_id: Primary token identity (owner)
            alias: Alias name (must be globally unique)
            role: Optional role metadata
            metadata: Additional metadata

        Returns:
            Created alias record

        Raises:
            DuplicateKeyError: If alias already exists (globally)
            ValueError: If validation fails
        """
        # Create alias with token_id as registered_by (enforced by schema)
        alias_record = DakbAgentAlias(
            token_id=token_id,
            alias=alias,
            role=role,
            registered_by=token_id,  # Must match token_id
            metadata=metadata or {}
        )

        try:
            doc = alias_record.model_dump()
            self.collection.insert_one(doc)
            logger.info(
                f"Alias registered: '{alias}' for token '{token_id}' "
                f"(role: {role or 'none'})"
            )
            return alias_record

        except DuplicateKeyError:
            logger.warning(f"Alias '{alias}' already exists (globally unique constraint)")
            raise DuplicateKeyError(
                f"Alias '{alias}' is already registered. "
                "Aliases must be globally unique across all tokens."
            )

    def get_aliases_for_token(self, token_id: str, active_only: bool = True) -> list[DakbAgentAlias]:
        """
        Get all aliases registered to a token.

        Args:
            token_id: Primary token identity
            active_only: If True, only return active aliases

        Returns:
            List of alias records for the token
        """
        query: dict[str, Any] = {"token_id": token_id}
        if active_only:
            query["is_active"] = True

        cursor = self.collection.find(query).sort("registered_at", -1)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbAgentAlias(**doc))

        return results

    def resolve_alias(self, alias_or_agent_id: str) -> Optional[str]:
        """
        Resolve an alias or agent_id to its owning token_id.

        This is the primary method for message routing.
        Supports both:
        1. Alias resolution (e.g., "Coordinator" -> "claude-code-agent")
        2. Direct agent_id routing (e.g., "codex-agent" -> "codex-agent")

        Args:
            alias_or_agent_id: Alias name OR agent_id to resolve

        Returns:
            token_id/agent_id for message routing, None if not found
        """
        # First, try to resolve as an alias
        doc = self.collection.find_one({
            "alias": alias_or_agent_id,
            "is_active": True
        })

        if doc:
            token_id = doc.get("token_id")
            logger.debug(f"Alias '{alias_or_agent_id}' resolved to token '{token_id}'")
            return token_id

        # If not found as alias, check if it's a direct agent_id
        if self.agents_collection is not None:
            agent_doc = self.agents_collection.find_one({
                "agent_id": alias_or_agent_id,
                "status": {"$ne": "suspended"}  # Don't route to suspended agents
            })

            if agent_doc:
                logger.debug(f"Direct agent_id '{alias_or_agent_id}' found, routing directly")
                return alias_or_agent_id

        logger.debug(f"'{alias_or_agent_id}' not found as alias or agent_id")
        return None

    def deactivate_alias(self, token_id: str, alias: str) -> bool:
        """
        Deactivate an alias (soft delete).

        Only the owning token can deactivate its aliases.
        Deactivated aliases no longer route messages but remain in the database.

        Args:
            token_id: Token requesting deactivation (must be owner)
            alias: Alias to deactivate

        Returns:
            True if deactivated successfully, False if not found or not owned
        """
        result = self.collection.update_one(
            {
                "alias": alias,
                "token_id": token_id,  # Ownership check
                "is_active": True
            },
            {
                "$set": {
                    "is_active": False,
                }
            }
        )

        if result.modified_count > 0:
            logger.info(f"Alias '{alias}' deactivated by token '{token_id}'")
            return True

        logger.warning(
            f"Failed to deactivate alias '{alias}' for token '{token_id}' "
            "(not found, not owned, or already inactive)"
        )
        return False

    def is_alias_available(self, alias: str) -> bool:
        """
        Check if an alias is available for registration.

        An alias is available if it doesn't exist OR exists but is inactive.
        Note: Inactive aliases can potentially be reclaimed, but this requires
        explicit cleanup of the old record first.

        Args:
            alias: Alias name to check

        Returns:
            True if alias is available (doesn't exist), False otherwise
        """
        # Check if any document with this alias exists (active or inactive)
        doc = self.collection.find_one({"alias": alias})
        available = doc is None

        if not available:
            is_active = doc.get("is_active", True)
            owner = doc.get("token_id", "unknown")
            logger.debug(
                f"Alias '{alias}' is not available "
                f"(owned by '{owner}', active={is_active})"
            )

        return available

    def get_by_id(self, alias_id: str) -> Optional[DakbAgentAlias]:
        """
        Get alias by its unique alias_id.

        Args:
            alias_id: Unique alias identifier

        Returns:
            Alias record or None if not found
        """
        doc = self.collection.find_one({"alias_id": alias_id})
        if doc:
            doc.pop("_id", None)
            return DakbAgentAlias(**doc)
        return None

    def get_by_alias(self, alias: str) -> Optional[DakbAgentAlias]:
        """
        Get alias record by alias name.

        Unlike resolve_alias(), this returns the full record
        regardless of active status.

        Args:
            alias: Alias name

        Returns:
            Alias record or None if not found
        """
        doc = self.collection.find_one({"alias": alias})
        if doc:
            doc.pop("_id", None)
            return DakbAgentAlias(**doc)
        return None

    def update_alias(
        self,
        token_id: str,
        alias: str,
        data: AliasUpdate
    ) -> Optional[DakbAgentAlias]:
        """
        Update an alias's metadata.

        Only the owning token can update its aliases.

        Args:
            token_id: Token requesting update (must be owner)
            alias: Alias to update
            data: Update data

        Returns:
            Updated alias record or None if not found/not owned
        """
        update_fields = data.model_dump(exclude_unset=True)
        if not update_fields:
            return self.get_by_alias(alias)

        result = self.collection.find_one_and_update(
            {
                "alias": alias,
                "token_id": token_id  # Ownership check
            },
            {"$set": update_fields},
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(f"Alias '{alias}' updated by token '{token_id}'")
            return DakbAgentAlias(**result)

        logger.warning(f"Failed to update alias '{alias}' for token '{token_id}'")
        return None

    def reactivate_alias(self, token_id: str, alias: str) -> bool:
        """
        Reactivate a previously deactivated alias.

        Only the owning token can reactivate its aliases.

        Args:
            token_id: Token requesting reactivation (must be owner)
            alias: Alias to reactivate

        Returns:
            True if reactivated successfully
        """
        result = self.collection.update_one(
            {
                "alias": alias,
                "token_id": token_id,  # Ownership check
                "is_active": False
            },
            {
                "$set": {"is_active": True}
            }
        )

        if result.modified_count > 0:
            logger.info(f"Alias '{alias}' reactivated by token '{token_id}'")
            return True

        return False

    def delete_alias(self, token_id: str, alias: str) -> bool:
        """
        Permanently delete an alias (hard delete).

        Only the owning token can delete its aliases.
        Use with caution - prefer deactivate_alias() for soft delete.

        Args:
            token_id: Token requesting deletion (must be owner)
            alias: Alias to delete

        Returns:
            True if deleted successfully
        """
        result = self.collection.delete_one({
            "alias": alias,
            "token_id": token_id  # Ownership check
        })

        if result.deleted_count > 0:
            logger.info(f"Alias '{alias}' permanently deleted by token '{token_id}'")
            return True

        return False

    def count_aliases_for_token(self, token_id: str, active_only: bool = True) -> int:
        """
        Count aliases for a token.

        Args:
            token_id: Primary token identity
            active_only: If True, only count active aliases

        Returns:
            Number of aliases
        """
        query: dict[str, Any] = {"token_id": token_id}
        if active_only:
            query["is_active"] = True

        return self.collection.count_documents(query)

    def get_alias_names_for_token(self, token_id: str, active_only: bool = True) -> list[str]:
        """
        Get just the alias names (strings) for a token.

        This is optimized for inbox queries where we only need alias names,
        not full alias records. Supports Phase 4 shared inbox functionality.

        Args:
            token_id: Primary token identity
            active_only: If True, only return active aliases (default True)

        Returns:
            List of alias name strings for the token

        Example:
            >>> alias_names = repo.get_alias_names_for_token("claude-code-agent")
            >>> # Returns: ["Coordinator", "Reviewer", "Backend"]
        """
        aliases = self.get_aliases_for_token(token_id, active_only=active_only)
        return [a.alias for a in aliases]

    def list_all_aliases(
        self,
        active_only: bool = True,
        limit: int = 100,
        skip: int = 0
    ) -> list[DakbAgentAlias]:
        """
        List all aliases in the system.

        Useful for admin/monitoring purposes.

        Args:
            active_only: If True, only return active aliases
            limit: Maximum results
            skip: Number of results to skip

        Returns:
            List of alias records
        """
        query: dict[str, Any] = {}
        if active_only:
            query["is_active"] = True

        cursor = self.collection.find(query).sort("registered_at", -1).skip(skip).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbAgentAlias(**doc))

        return results


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def get_dakb_repositories(mongo_client: MongoClient, db_name: str = "dakb") -> dict:
    """
    Create all DAKB repository instances.

    Args:
        mongo_client: MongoDB client
        db_name: Database name

    Returns:
        Dictionary with all repository instances
    """
    collections = DAKBCollections(mongo_client, db_name)

    return {
        "collections": collections,
        "knowledge": KnowledgeRepository(collections.knowledge),
        "messages": MessageRepository(collections.messages),
        "agents": AgentRepository(collections.agents),
        "sessions": SessionRepository(collections.sessions),
        "audit": AuditRepository(collections.audit_log),
        # Step 2.3: Voting & Reputation
        "reputation": ReputationRepository(collections.reputation),
        "quality": QualityRepository(collections.quality),
        "flags": FlagRepository(collections.flags),
        # Agent Alias System (Token Team) - with agents collection for direct agent_id routing
        "aliases": AliasRepository(collections.aliases, agents_collection=collections.agents),
        # Self-Registration v1.0 (Invite-Only)
        "invite_tokens": InviteTokenRepository(collections.invite_tokens),
        "registration_audit": RegistrationAuditRepository(collections.registration_audit),
    }
