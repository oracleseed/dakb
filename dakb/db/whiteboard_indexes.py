"""
Whiteboard MongoDB Index Definitions

Index definitions for whiteboard collections: boards, sections, and snapshots.
Follows the same pattern as dakb/db/indexes.py.

Collections:
- dakb_whiteboard_boards: 2 indexes
- dakb_whiteboard_sections: 5 indexes
- dakb_whiteboard_snapshots: 3 indexes
"""

import logging

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import OperationFailure, PyMongoError

logger = logging.getLogger(__name__)

# =============================================================================
# INDEX DEFINITIONS
# =============================================================================

# dakb_whiteboard_boards indexes
BOARD_INDEXES = [
    {
        "name": "board_id_unique",
        "keys": [("board_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "board_type",
        "keys": [("board_type", ASCENDING)],
    },
]

# dakb_whiteboard_sections indexes
SECTION_INDEXES = [
    {
        "name": "board_section_unique",
        "keys": [("board_id", ASCENDING), ("section_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "board_section_type",
        "keys": [("board_id", ASCENDING), ("section_type", ASCENDING)],
    },
    {
        "name": "owner_id",
        "keys": [("owner_id", ASCENDING)],
    },
    {
        "name": "updated_at_desc",
        "keys": [("updated_at", DESCENDING)],
    },
    {
        "name": "expires_at",
        "keys": [("expires_at", ASCENDING)],
    },
]

# dakb_whiteboard_snapshots indexes
SNAPSHOT_INDEXES = [
    {
        "name": "snapshot_id_unique",
        "keys": [("snapshot_id", ASCENDING)],
        "unique": True,
    },
    {
        "name": "board_snapshot_at",
        "keys": [("board_id", ASCENDING), ("snapshot_at", DESCENDING)],
    },
    {
        "name": "snapshot_at_desc",
        "keys": [("snapshot_at", DESCENDING)],
    },
]

# =============================================================================
# COLLECTION INDEX MAP
# =============================================================================

COLLECTION_INDEX_MAP = {
    "dakb_whiteboard_boards": BOARD_INDEXES,
    "dakb_whiteboard_sections": SECTION_INDEXES,
    "dakb_whiteboard_snapshots": SNAPSHOT_INDEXES,
}

# =============================================================================
# INITIALIZATION
# =============================================================================


def initialize_whiteboard_indexes(db: Database) -> dict[str, int]:
    """
    Create all whiteboard indexes.

    Args:
        db: MongoDB database instance

    Returns:
        Dictionary mapping collection name to count of indexes created.
    """
    results = {}

    for collection_name, indexes in COLLECTION_INDEX_MAP.items():
        collection = db[collection_name]
        created = 0

        for index_def in indexes:
            try:
                name = index_def["name"]
                keys = index_def["keys"]

                options = {"name": name}
                if index_def.get("unique"):
                    options["unique"] = True

                collection.create_index(keys, **options)
                created += 1
                logger.info(f"Created index '{name}' on {collection_name}")

            except OperationFailure as e:
                if "already exists" in str(e) or "An equivalent index already exists" in str(e):
                    created += 1
                    logger.debug(f"Index '{index_def['name']}' already exists on {collection_name}")
                else:
                    logger.error(f"Failed to create index '{index_def['name']}' on {collection_name}: {e}")

            except PyMongoError as e:
                logger.error(f"Failed to create index '{index_def['name']}' on {collection_name}: {e}")

        results[collection_name] = created

    total = sum(results.values())
    logger.info(f"Whiteboard index creation complete: {total} indexes across {len(results)} collections")

    return results
