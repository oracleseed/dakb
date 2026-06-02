"""
DAKB MongoDB Index Management

Functions for creating and managing indexes on DAKB collections.
Includes unique indexes, compound indexes, TTL indexes, and text indexes.

Version: 1.6
Created: 2025-12-07
Updated: 2025-12-11 (Added invite tokens and registration audit indexes)

Index Strategy:
- Unique indexes on primary IDs
- Compound indexes for common query patterns
- TTL indexes for automatic expiration
- Text indexes for full-text search on knowledge

Collections:
- dakb_knowledge: 8 indexes
- dakb_messages: 4 indexes
- dakb_agents: 4 indexes
- dakb_sessions: 3 indexes
- dakb_audit_log: 4 indexes
- dakb_agent_aliases: 5 indexes (Token Team with Agent Aliases)
- dakb_invite_tokens: 5 indexes (Self-Registration v1.0)
- dakb_registration_audit: 5 indexes (Self-Registration v1.0, 90-day TTL)
- dakb_vault_files: 6 indexes (HIVE File Vault, purge_after TTL)
- dakb_knowledge_threads: 5 indexes (Knowledge Threads)
- dakb_knowledge_versions: 2 indexes (Knowledge Version History)
- dakb_tasks: 5 indexes (Real-Time Communication: task delegation, expires_at TTL)
- dakb_chat_sessions: 4 indexes (Chat Bridge: external chat session bindings)
- dakb_alert_config: 2 indexes (Chat Bridge: per-user alert routing)
- dakb_bridge_links: 4 indexes (Session Bridge: session<->chat link records)
- dakb_bridge_queue: 2 indexes (Session Bridge: offline queue, queued_at 7d TTL)
- dakb_whiteboard_boards: 2 indexes (Whiteboard, via whiteboard_indexes)
- dakb_whiteboard_sections: 5 indexes (Whiteboard, via whiteboard_indexes)
- dakb_whiteboard_snapshots: 3 indexes (Whiteboard, via whiteboard_indexes)
"""

import logging

from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import OperationFailure, PyMongoError

from .whiteboard_indexes import initialize_whiteboard_indexes

logger = logging.getLogger(__name__)

# =============================================================================
# INDEX DEFINITIONS
# =============================================================================

# dakb_knowledge indexes
KNOWLEDGE_INDEXES = [
    {
        "name": "knowledge_id_unique",
        "keys": [("knowledge_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "agent_id",
        "keys": [("source.agent_id", ASCENDING)],
    },
    {
        "name": "category_status",
        "keys": [("category", ASCENDING), ("status", ASCENDING)],
    },
    {
        "name": "access_level",
        "keys": [("access_level", ASCENDING)],
    },
    {
        "name": "tags",
        "keys": [("tags", ASCENDING)],
    },
    {
        "name": "created_at",
        "keys": [("created_at", DESCENDING)],
    },
    {
        "name": "votes_helpful",
        "keys": [("votes.helpful", DESCENDING)],
    },
    {
        "name": "expires_at_ttl",
        "keys": [("expires_at", ASCENDING)],
        "expireAfterSeconds": 0,  # TTL index - expires at the time specified
        "partialFilterExpression": {"expires_at": {"$exists": True, "$ne": None}},
    },
    {
        "name": "title_content_text",
        "keys": [("title", TEXT), ("content", TEXT), ("tags", TEXT), ("keywords", TEXT)],
        "weights": {"title": 10, "content": 5, "tags": 3, "keywords": 2},
        "default_language": "english",
    },
]

# dakb_messages indexes
MESSAGES_INDEXES = [
    {
        "name": "message_id_unique",
        "keys": [("message_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "to_agent_status",
        "keys": [("to_agent", ASCENDING), ("status", ASCENDING)],
    },
    {
        "name": "to_topic",
        "keys": [("to_topic", ASCENDING)],
    },
    {
        "name": "from_agent",
        "keys": [("from_agent", ASCENDING)],
    },
    {
        "name": "expires_at_ttl",
        "keys": [("expires_at", ASCENDING)],
        "expireAfterSeconds": 0,
        "partialFilterExpression": {"expires_at": {"$exists": True, "$ne": None}},
    },
]

# dakb_agents indexes
AGENTS_INDEXES = [
    {
        "name": "agent_id_unique",
        "keys": [("agent_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "agent_type",
        "keys": [("agent_type", ASCENDING)],
    },
    {
        "name": "status",
        "keys": [("status", ASCENDING)],
    },
    {
        "name": "last_seen",
        "keys": [("last_seen", DESCENDING)],
    },
]

# dakb_sessions indexes
SESSIONS_INDEXES = [
    {
        "name": "session_id_unique",
        "keys": [("session_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "agent_id_ended_at",
        "keys": [("agent_id", ASCENDING), ("ended_at", ASCENDING)],
    },
    {
        "name": "started_at",
        "keys": [("started_at", DESCENDING)],
    },
]

# dakb_audit_log indexes
AUDIT_LOG_INDEXES = [
    {
        "name": "log_id_unique",
        "keys": [("log_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "timestamp",
        "keys": [("timestamp", DESCENDING)],
    },
    {
        "name": "agent_id_action",
        "keys": [("agent_id", ASCENDING), ("action", ASCENDING)],
    },
    {
        "name": "resource_type_id",
        "keys": [("resource_type", ASCENDING), ("resource_id", ASCENDING)],
    },
    {
        "name": "expires_at_ttl",
        "keys": [("expires_at", ASCENDING)],
        "expireAfterSeconds": 0,  # 90 days expiry set in schema
        "partialFilterExpression": {"expires_at": {"$exists": True, "$ne": None}},
    },
]

# dakb_agent_aliases indexes (Token Team with Agent Aliases)
ALIASES_INDEXES = [
    {
        # Primary unique identifier
        "name": "alias_id_unique",
        "keys": [("alias_id", ASCENDING)],
        "unique": True,
    },
    {
        # CRITICAL: Global alias uniqueness - ensures no two tokens can have the same alias
        "name": "alias_unique",
        "keys": [("alias", ASCENDING)],
        "unique": True,
    },
    {
        # Compound index for token lookup with alias (ownership verification)
        "name": "token_id_alias",
        "keys": [("token_id", ASCENDING), ("alias", ASCENDING)],
    },
    {
        # Index for filtering by active status
        "name": "is_active",
        "keys": [("is_active", ASCENDING)],
    },
    {
        # Index for listing aliases by token (get_aliases_for_token)
        "name": "token_id_active_registered",
        "keys": [("token_id", ASCENDING), ("is_active", ASCENDING), ("registered_at", DESCENDING)],
    },
]

# dakb_invite_tokens indexes (Self-Registration v1.0)
INVITE_TOKENS_INDEXES = [
    {
        # Primary unique identifier
        "name": "invite_token_unique",
        "keys": [("invite_token", ASCENDING)],
        "unique": True,
    },
    {
        # Index for filtering by status
        "name": "status",
        "keys": [("status", ASCENDING)],
    },
    {
        # Compound index for admin queries (list by creator)
        "name": "created_by_status",
        "keys": [("created_by", ASCENDING), ("status", ASCENDING)],
    },
    {
        # Index for listing by creation date
        "name": "created_at",
        "keys": [("created_at", DESCENDING)],
    },
    {
        # TTL index for automatic token cleanup
        # Tokens will be deleted some time after they expire
        # (MongoDB TTL indexes run approximately every 60 seconds)
        "name": "expires_at_ttl",
        "keys": [("expires_at", ASCENDING)],
        "expireAfterSeconds": 0,  # Expires at the time specified in expires_at
        "partialFilterExpression": {"expires_at": {"$exists": True, "$ne": None}},
    },
]

# dakb_registration_audit indexes (Self-Registration v1.0)
REGISTRATION_AUDIT_INDEXES = [
    {
        # Primary unique identifier
        "name": "audit_id_unique",
        "keys": [("audit_id", ASCENDING)],
        "unique": True,
    },
    {
        # Index for chronological queries
        "name": "timestamp",
        "keys": [("timestamp", DESCENDING)],
    },
    {
        # Compound index for agent queries (actor or target)
        "name": "actor_agent_id_action",
        "keys": [("actor_agent_id", ASCENDING), ("action", ASCENDING)],
    },
    {
        # Index for finding entries by target token
        "name": "target_token",
        "keys": [("target_token", ASCENDING)],
    },
    {
        # TTL index for automatic 90-day cleanup
        # Registration audit entries auto-expire per GDPR/retention policy
        "name": "expires_at_ttl",
        "keys": [("expires_at", ASCENDING)],
        "expireAfterSeconds": 0,  # 90-day TTL set in schema's expires_at field
        "partialFilterExpression": {"expires_at": {"$exists": True, "$ne": None}},
    },
]

# dakb_vault_files indexes (HIVE File Vault)
VAULT_FILE_INDEXES = [
    {
        "name": "file_id_unique",
        "keys": [("file_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "knowledge_id",
        "keys": [("knowledge_id", ASCENDING)],
    },
    {
        "name": "status",
        "keys": [("status", ASCENDING)],
    },
    {
        "name": "uploaded_by",
        "keys": [("uploaded_by", ASCENDING)],
    },
    {
        "name": "purge_after_ttl",
        "keys": [("purge_after", ASCENDING)],
        "expireAfterSeconds": 0,  # MongoDB auto-deletes when purge_after passes
        "partialFilterExpression": {"purge_after": {"$exists": True, "$ne": None}},
    },
    {
        "name": "mime_type_uploaded_at",
        "keys": [("mime_type", ASCENDING), ("uploaded_at", DESCENDING)],
    },
]

# dakb_knowledge_threads indexes (Knowledge Threads)
THREADS_INDEXES = [
    {
        "name": "thread_id_unique",
        "keys": [("thread_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "knowledge_id_created_at",
        "keys": [("knowledge_id", ASCENDING), ("created_at", DESCENDING)],
    },
    {
        "name": "knowledge_id_type",
        "keys": [("knowledge_id", ASCENDING), ("type", ASCENDING)],
    },
    {
        "name": "knowledge_id_parent_id",
        "keys": [("knowledge_id", ASCENDING), ("parent_id", ASCENDING)],
    },
    {
        "name": "author_agent_id",
        "keys": [("author_agent_id", ASCENDING)],
    },
]

# dakb_knowledge_versions indexes (Knowledge Version History)
VERSIONS_INDEXES = [
    {
        "name": "version_id_unique",
        "keys": [("version_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "knowledge_id_version",
        "keys": [("knowledge_id", ASCENDING), ("version", DESCENDING)],
    },
]

# dakb_tasks indexes (Real-Time Communication: task delegation)
TASKS_INDEXES = [
    {
        "name": "task_id_unique",
        "keys": [("task_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "status",
        "keys": [("status", ASCENDING)],
    },
    {
        "name": "assigned_to",
        "keys": [("assigned_to", ASCENDING)],
    },
    {
        "name": "requester_id_created_at",
        "keys": [("requester_id", ASCENDING), ("created_at", DESCENDING)],
    },
    {
        # TTL index for automatic cleanup of expired tasks
        "name": "expires_at_ttl",
        "keys": [("expires_at", ASCENDING)],
        "expireAfterSeconds": 0,  # Expires at the time specified in expires_at
        "partialFilterExpression": {"expires_at": {"$exists": True, "$ne": None}},
    },
]

# dakb_chat_sessions indexes (Chat Bridge: external chat session bindings)
CHAT_SESSIONS_INDEXES = [
    {
        # Composite chat ID is the lookup key (e.g. "telegram:12345")
        "name": "composite_chat_id_unique",
        "keys": [("composite_chat_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "session_id",
        "keys": [("session_id", ASCENDING)],
    },
    {
        "name": "platform_external_chat_id",
        "keys": [("platform", ASCENDING), ("external_chat_id", ASCENDING)],
    },
    {
        "name": "last_active",
        "keys": [("last_active", DESCENDING)],
    },
]

# dakb_alert_config indexes (Chat Bridge: per-user alert routing)
ALERT_CONFIG_INDEXES = [
    {
        # One alert-config document per user
        "name": "user_id_unique",
        "keys": [("user_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "updated_at",
        "keys": [("updated_at", DESCENDING)],
    },
]

# dakb_bridge_links indexes (Session Bridge: session <-> chat link records)
BRIDGE_LINKS_INDEXES = [
    {
        # Each session may link a given chat at most once
        "name": "session_id_composite_chat_id_unique",
        "keys": [("session_id", ASCENDING), ("composite_chat_id", ASCENDING)],
        "unique": True,
    },
    {
        # Ownership check: {session_id, agent_id, active}
        "name": "session_id_agent_id_active",
        "keys": [
            ("session_id", ASCENDING),
            ("agent_id", ASCENDING),
            ("active", ASCENDING),
        ],
    },
    {
        # List active links for a session: {session_id, active}
        "name": "session_id_active",
        "keys": [("session_id", ASCENDING), ("active", ASCENDING)],
    },
    {
        "name": "composite_chat_id",
        "keys": [("composite_chat_id", ASCENDING)],
    },
]

# dakb_bridge_queue indexes (Session Bridge: offline message queue)
BRIDGE_QUEUE_INDEXES = [
    {
        # Backlog delivery query: find by session_id sorted by queued_at
        "name": "session_id_queued_at",
        "keys": [("session_id", ASCENDING), ("queued_at", ASCENDING)],
    },
    {
        # TTL index: offline messages auto-expire after 7 days
        "name": "queued_at_ttl",
        "keys": [("queued_at", ASCENDING)],
        "expireAfterSeconds": 604800,  # 7 days
        "partialFilterExpression": {"queued_at": {"$exists": True, "$ne": None}},
    },
]


# =============================================================================
# INDEX CREATION FUNCTIONS
# =============================================================================

def create_index(collection: Collection, index_def: dict) -> bool:
    """
    Create a single index on a collection.

    Args:
        collection: MongoDB collection
        index_def: Index definition dictionary

    Returns:
        True if index created successfully, False otherwise
    """
    try:
        # Extract index properties
        name = index_def["name"]
        keys = index_def["keys"]

        # Build index options
        options = {"name": name}

        if index_def.get("unique"):
            options["unique"] = True

        if "expireAfterSeconds" in index_def:
            options["expireAfterSeconds"] = index_def["expireAfterSeconds"]

        if "partialFilterExpression" in index_def:
            options["partialFilterExpression"] = index_def["partialFilterExpression"]

        if "weights" in index_def:
            options["weights"] = index_def["weights"]

        if "default_language" in index_def:
            options["default_language"] = index_def["default_language"]

        # Create the index
        collection.create_index(keys, **options)
        logger.info(f"Created index '{name}' on {collection.name}")
        return True

    except OperationFailure as e:
        if "already exists" in str(e) or "An equivalent index already exists" in str(e):
            logger.debug(f"Index '{index_def['name']}' already exists on {collection.name}")
            return True
        logger.error(f"Failed to create index '{index_def['name']}' on {collection.name}: {e}")
        return False

    except PyMongoError as e:
        logger.error(f"Failed to create index '{index_def['name']}' on {collection.name}: {e}")
        return False


def create_collection_indexes(collection: Collection, indexes: list[dict]) -> dict:
    """
    Create all indexes for a collection.

    Args:
        collection: MongoDB collection
        indexes: List of index definitions

    Returns:
        Dictionary with success/failure counts
    """
    results = {"success": 0, "failed": 0, "skipped": 0}

    for index_def in indexes:
        if create_index(collection, index_def):
            results["success"] += 1
        else:
            results["failed"] += 1

    return results


def create_knowledge_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_knowledge collection."""
    logger.info("Creating indexes for dakb_knowledge...")
    return create_collection_indexes(collection, KNOWLEDGE_INDEXES)


def create_messages_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_messages collection."""
    logger.info("Creating indexes for dakb_messages...")
    return create_collection_indexes(collection, MESSAGES_INDEXES)


def create_agents_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_agents collection."""
    logger.info("Creating indexes for dakb_agents...")
    return create_collection_indexes(collection, AGENTS_INDEXES)


def create_sessions_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_sessions collection."""
    logger.info("Creating indexes for dakb_sessions...")
    return create_collection_indexes(collection, SESSIONS_INDEXES)


def create_audit_log_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_audit_log collection."""
    logger.info("Creating indexes for dakb_audit_log...")
    return create_collection_indexes(collection, AUDIT_LOG_INDEXES)


def create_aliases_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_agent_aliases collection."""
    logger.info("Creating indexes for dakb_agent_aliases...")
    return create_collection_indexes(collection, ALIASES_INDEXES)


def create_invite_tokens_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_invite_tokens collection."""
    logger.info("Creating indexes for dakb_invite_tokens...")
    return create_collection_indexes(collection, INVITE_TOKENS_INDEXES)


def create_registration_audit_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_registration_audit collection."""
    logger.info("Creating indexes for dakb_registration_audit...")
    return create_collection_indexes(collection, REGISTRATION_AUDIT_INDEXES)


def create_vault_file_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_vault_files collection."""
    logger.info("Creating indexes for dakb_vault_files...")
    return create_collection_indexes(collection, VAULT_FILE_INDEXES)


def create_threads_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_knowledge_threads collection."""
    logger.info("Creating indexes for dakb_knowledge_threads...")
    return create_collection_indexes(collection, THREADS_INDEXES)


def create_versions_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_knowledge_versions collection."""
    logger.info("Creating indexes for dakb_knowledge_versions...")
    return create_collection_indexes(collection, VERSIONS_INDEXES)


def create_tasks_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_tasks collection."""
    logger.info("Creating indexes for dakb_tasks...")
    return create_collection_indexes(collection, TASKS_INDEXES)


def create_chat_sessions_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_chat_sessions collection."""
    logger.info("Creating indexes for dakb_chat_sessions...")
    return create_collection_indexes(collection, CHAT_SESSIONS_INDEXES)


def create_alert_config_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_alert_config collection."""
    logger.info("Creating indexes for dakb_alert_config...")
    return create_collection_indexes(collection, ALERT_CONFIG_INDEXES)


def create_bridge_links_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_bridge_links collection."""
    logger.info("Creating indexes for dakb_bridge_links...")
    return create_collection_indexes(collection, BRIDGE_LINKS_INDEXES)


def create_bridge_queue_indexes(collection: Collection) -> dict:
    """Create all indexes for dakb_bridge_queue collection."""
    logger.info("Creating indexes for dakb_bridge_queue...")
    return create_collection_indexes(collection, BRIDGE_QUEUE_INDEXES)


def create_all_indexes(db: Database) -> dict:
    """
    Create all DAKB indexes.

    Args:
        db: MongoDB database instance

    Returns:
        Dictionary with results per collection
    """
    results = {}

    # Knowledge indexes
    results["dakb_knowledge"] = create_knowledge_indexes(db["dakb_knowledge"])

    # Messages indexes
    results["dakb_messages"] = create_messages_indexes(db["dakb_messages"])

    # Agents indexes
    results["dakb_agents"] = create_agents_indexes(db["dakb_agents"])

    # Sessions indexes
    results["dakb_sessions"] = create_sessions_indexes(db["dakb_sessions"])

    # Audit log indexes
    results["dakb_audit_log"] = create_audit_log_indexes(db["dakb_audit_log"])

    # Agent aliases indexes (Token Team)
    results["dakb_agent_aliases"] = create_aliases_indexes(db["dakb_agent_aliases"])

    # Invite tokens indexes (Self-Registration v1.0)
    results["dakb_invite_tokens"] = create_invite_tokens_indexes(db["dakb_invite_tokens"])

    # Registration audit indexes (Self-Registration v1.0)
    results["dakb_registration_audit"] = create_registration_audit_indexes(db["dakb_registration_audit"])

    # Vault Files indexes (HIVE File Vault)
    results["dakb_vault_files"] = create_vault_file_indexes(db["dakb_vault_files"])

    # Knowledge Threads indexes
    results["dakb_knowledge_threads"] = create_threads_indexes(db["dakb_knowledge_threads"])

    # Knowledge Versions indexes
    results["dakb_knowledge_versions"] = create_versions_indexes(db["dakb_knowledge_versions"])

    # Task delegation indexes (Real-Time Communication)
    results["dakb_tasks"] = create_tasks_indexes(db["dakb_tasks"])

    # Chat Bridge indexes
    results["dakb_chat_sessions"] = create_chat_sessions_indexes(db["dakb_chat_sessions"])
    results["dakb_alert_config"] = create_alert_config_indexes(db["dakb_alert_config"])

    # Session Bridge indexes
    results["dakb_bridge_links"] = create_bridge_links_indexes(db["dakb_bridge_links"])
    results["dakb_bridge_queue"] = create_bridge_queue_indexes(db["dakb_bridge_queue"])

    # Whiteboard indexes
    wb_results = initialize_whiteboard_indexes(db)
    for coll_name, count in wb_results.items():
        results[coll_name] = {"success": count, "failed": 0}

    # Summary
    total_success = sum(r["success"] for r in results.values())
    total_failed = sum(r["failed"] for r in results.values())

    logger.info(f"Index creation complete: {total_success} success, {total_failed} failed")

    return results


# =============================================================================
# INDEX MANAGEMENT FUNCTIONS
# =============================================================================

def list_indexes(collection: Collection) -> list[dict]:
    """
    List all indexes on a collection.

    Args:
        collection: MongoDB collection

    Returns:
        List of index information dictionaries
    """
    indexes = []
    for index_info in collection.list_indexes():
        indexes.append({
            "name": index_info.get("name"),
            "keys": dict(index_info.get("key", {})),
            "unique": index_info.get("unique", False),
            "sparse": index_info.get("sparse", False),
            "expireAfterSeconds": index_info.get("expireAfterSeconds"),
        })
    return indexes


def drop_index(collection: Collection, index_name: str) -> bool:
    """
    Drop a specific index from a collection.

    Args:
        collection: MongoDB collection
        index_name: Name of the index to drop

    Returns:
        True if dropped successfully
    """
    try:
        if index_name == "_id_":
            logger.warning("Cannot drop _id_ index")
            return False

        collection.drop_index(index_name)
        logger.info(f"Dropped index '{index_name}' from {collection.name}")
        return True

    except OperationFailure as e:
        if "index not found" in str(e).lower():
            logger.debug(f"Index '{index_name}' not found on {collection.name}")
            return True
        logger.error(f"Failed to drop index '{index_name}': {e}")
        return False


def drop_all_dakb_indexes(db: Database, keep_id: bool = True) -> dict:
    """
    Drop all DAKB indexes (except _id_ if keep_id is True).

    USE WITH CAUTION - this will affect query performance.

    Args:
        db: MongoDB database
        keep_id: If True, preserve _id_ indexes

    Returns:
        Dictionary with results per collection
    """
    collections = [
        "dakb_knowledge",
        "dakb_messages",
        "dakb_agents",
        "dakb_sessions",
        "dakb_audit_log",
        "dakb_agent_aliases",
        "dakb_invite_tokens",
        "dakb_registration_audit",
        "dakb_vault_files",
        "dakb_knowledge_threads",
        "dakb_knowledge_versions",
        "dakb_tasks",
        "dakb_chat_sessions",
        "dakb_alert_config",
        "dakb_bridge_links",
        "dakb_bridge_queue",
        "dakb_whiteboard_boards",
        "dakb_whiteboard_sections",
        "dakb_whiteboard_snapshots",
    ]

    results = {}

    for coll_name in collections:
        collection = db[coll_name]
        dropped = 0
        failed = 0

        for index_info in collection.list_indexes():
            index_name = index_info.get("name")
            if keep_id and index_name == "_id_":
                continue

            if drop_index(collection, index_name):
                dropped += 1
            else:
                failed += 1

        results[coll_name] = {"dropped": dropped, "failed": failed}
        logger.info(f"Dropped {dropped} indexes from {coll_name}")

    return results


def rebuild_indexes(db: Database) -> dict:
    """
    Rebuild all DAKB indexes by dropping and recreating them.

    USE WITH CAUTION - temporary performance impact during rebuild.

    Args:
        db: MongoDB database

    Returns:
        Dictionary with drop and create results
    """
    logger.warning("Starting full index rebuild for DAKB collections...")

    # Drop all existing indexes
    drop_results = drop_all_dakb_indexes(db)

    # Recreate all indexes
    create_results = create_all_indexes(db)

    return {
        "dropped": drop_results,
        "created": create_results,
    }


def verify_indexes(db: Database) -> dict:
    """
    Verify all required DAKB indexes exist.

    Args:
        db: MongoDB database

    Returns:
        Dictionary with verification status per collection
    """
    expected = {
        "dakb_knowledge": {idx["name"] for idx in KNOWLEDGE_INDEXES},
        "dakb_messages": {idx["name"] for idx in MESSAGES_INDEXES},
        "dakb_agents": {idx["name"] for idx in AGENTS_INDEXES},
        "dakb_sessions": {idx["name"] for idx in SESSIONS_INDEXES},
        "dakb_audit_log": {idx["name"] for idx in AUDIT_LOG_INDEXES},
        "dakb_agent_aliases": {idx["name"] for idx in ALIASES_INDEXES},
        "dakb_invite_tokens": {idx["name"] for idx in INVITE_TOKENS_INDEXES},
        "dakb_registration_audit": {idx["name"] for idx in REGISTRATION_AUDIT_INDEXES},
        "dakb_vault_files": {idx["name"] for idx in VAULT_FILE_INDEXES},
        "dakb_knowledge_threads": {idx["name"] for idx in THREADS_INDEXES},
        "dakb_knowledge_versions": {idx["name"] for idx in VERSIONS_INDEXES},
        "dakb_tasks": {idx["name"] for idx in TASKS_INDEXES},
        "dakb_chat_sessions": {idx["name"] for idx in CHAT_SESSIONS_INDEXES},
        "dakb_alert_config": {idx["name"] for idx in ALERT_CONFIG_INDEXES},
        "dakb_bridge_links": {idx["name"] for idx in BRIDGE_LINKS_INDEXES},
        "dakb_bridge_queue": {idx["name"] for idx in BRIDGE_QUEUE_INDEXES},
    }

    results = {}

    for coll_name, expected_indexes in expected.items():
        collection = db[coll_name]
        existing = {idx.get("name") for idx in collection.list_indexes()}

        missing = expected_indexes - existing
        extra = existing - expected_indexes - {"_id_"}

        results[coll_name] = {
            "expected": len(expected_indexes),
            "existing": len(existing) - 1,  # Exclude _id_
            "missing": list(missing),
            "extra": list(extra),
            "complete": len(missing) == 0,
        }

    return results


# =============================================================================
# INITIALIZATION FUNCTION
# =============================================================================

def initialize_dakb_indexes(db: Database, verify: bool = True) -> dict:
    """
    Initialize all DAKB indexes with optional verification.

    This is the main entry point for setting up DAKB indexes.

    Args:
        db: MongoDB database
        verify: If True, verify indexes after creation

    Returns:
        Dictionary with creation and verification results
    """
    logger.info("Initializing DAKB indexes...")

    # Create all indexes
    create_results = create_all_indexes(db)

    result = {"creation": create_results}

    # Optionally verify
    if verify:
        verify_results = verify_indexes(db)
        result["verification"] = verify_results

        # Check for any incomplete collections
        incomplete = [
            name for name, status in verify_results.items()
            if not status["complete"]
        ]

        if incomplete:
            logger.warning(f"Incomplete index setup for: {incomplete}")
        else:
            logger.info("All DAKB indexes verified successfully")

    return result
