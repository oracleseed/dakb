"""
HIVE Whiteboard Repository

CRUD operations for whiteboard boards, sections, and snapshots.
Uses PyMongo Database and atomic operations for optimistic concurrency.

Version: 2.0
Created: 2026-03-18
"""

import logging
from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.database import Database

from .whiteboard_schemas import (
    BoardType,
    SectionType,
    SnapshotTrigger,
    SnapshotType,
    WhiteboardBoard,
    WhiteboardBoardCreate,
    WhiteboardSection,
    WhiteboardSectionCreate,
    WhiteboardSnapshot,
    WhiteboardSnapshotCreate,
)

logger = logging.getLogger(__name__)

# Collection names
COLL_BOARDS = "dakb_whiteboard_boards"
COLL_SECTIONS = "dakb_whiteboard_sections"
COLL_SNAPSHOTS = "dakb_whiteboard_snapshots"


# =============================================================================
# EXCEPTIONS
# =============================================================================

class VersionConflictError(Exception):
    """Raised when an optimistic locking version mismatch is detected."""

    def __init__(self, current_version: int):
        self.current_version = current_version
        super().__init__(
            f"Version conflict: document is at version {current_version}"
        )


# =============================================================================
# WHITEBOARD REPOSITORY
# =============================================================================

class WhiteboardRepository:
    """
    Repository for whiteboard CRUD operations across three collections:
    - dakb_whiteboard_boards
    - dakb_whiteboard_sections
    - dakb_whiteboard_snapshots

    Accepts a pymongo Database and accesses collections internally.
    """

    def __init__(self, db: Database):
        self.db = db
        self.boards = db[COLL_BOARDS]
        self.sections = db[COLL_SECTIONS]
        self.snapshots = db[COLL_SNAPSHOTS]

    # -------------------------------------------------------------------------
    # BOARD CRUD
    # -------------------------------------------------------------------------

    def create_board(
        self,
        board_type: BoardType,
        display_name: str,
        project_path: str | None = None,
    ) -> dict:
        """
        Create a new whiteboard board.

        Returns:
            The inserted board document as a dict.

        Raises:
            DuplicateKeyError: If a board with the same board_id already exists.
        """
        # Use schema to auto-generate board_id
        create_data = WhiteboardBoardCreate(
            display_name=display_name,
            board_type=board_type,
            project_path=project_path,
        )
        now = datetime.now(timezone.utc)
        board = WhiteboardBoard(
            board_id=create_data.board_id,
            display_name=display_name,
            board_type=board_type,
            project_path=project_path,
            settings=create_data.settings,
            created_at=now,
            updated_at=now,
            section_ids=[],
        )
        doc = board.model_dump()
        self.boards.insert_one(doc)
        logger.info(f"Board created: {board.board_id}")
        doc.pop("_id", None)
        return doc

    def get_board(self, board_id: str) -> dict | None:
        """Get a board by ID. Returns dict or None."""
        doc = self.boards.find_one({"board_id": board_id})
        if doc:
            doc.pop("_id", None)
            return doc
        return None

    def list_boards(self, board_type: str | None = None) -> list[dict]:
        """List all boards, optionally filtered by type."""
        query: dict[str, Any] = {}
        if board_type:
            query["board_type"] = board_type
        cursor = self.boards.find(query).sort("created_at", -1)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results

    def update_compact_render(self, board_id: str, compact_render: str) -> bool:
        """Update the compact_render cache field on a board."""
        result = self.boards.update_one(
            {"board_id": board_id},
            {
                "$set": {
                    "compact_render": compact_render,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return result.modified_count > 0

    # -------------------------------------------------------------------------
    # SECTION CRUD
    # -------------------------------------------------------------------------

    def create_section(
        self,
        board_id: str,
        section_type: SectionType,
        owner_id: str | None = None,
        owner_alias: str | None = None,
        now: str | None = None,
        next: str | None = None,
        done_recent: list[str] | None = None,
        note: str | None = None,
    ) -> dict:
        """
        Create a new whiteboard section.

        Returns:
            The inserted section document as a dict.
        """
        create_data = WhiteboardSectionCreate(
            board_id=board_id,
            section_type=section_type,
            owner_id=owner_id,
            owner_alias=owner_alias,
            now=now,
            next=next,
            done_recent=done_recent or [],
            note=note,
        )
        ts = datetime.now(timezone.utc)
        section = WhiteboardSection(
            section_id=create_data.section_id,
            board_id=board_id,
            section_type=section_type,
            owner_id=owner_id,
            owner_alias=owner_alias,
            now=now,
            next=next,
            done_recent=done_recent or [],
            note=note,
            version=1,
            created_at=ts,
            updated_at=ts,
        )
        doc = section.model_dump()
        self.sections.insert_one(doc)

        # Add section_id to parent board
        self.boards.update_one(
            {"board_id": board_id},
            {"$addToSet": {"section_ids": create_data.section_id}},
        )

        logger.info(f"Section created: {create_data.section_id} on board {board_id}")
        doc.pop("_id", None)
        return doc

    def get_section(self, board_id: str, section_id: str) -> dict | None:
        """Get a section by board_id + section_id. Returns dict or None."""
        doc = self.sections.find_one({"board_id": board_id, "section_id": section_id})
        if doc:
            doc.pop("_id", None)
            return doc
        return None

    def get_sections(
        self,
        board_id: str,
        section_type: str | None = None,
    ) -> list[dict]:
        """Get all sections for a board, optionally filtered by type."""
        query: dict[str, Any] = {"board_id": board_id}
        if section_type:
            query["section_type"] = section_type
        cursor = self.sections.find(query).sort("section_type", 1)
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results

    def update_section(
        self,
        board_id: str,
        section_id: str,
        version: int,
        **fields: Any,
    ) -> dict:
        """
        Update a section with optimistic locking via find_one_and_update.

        Args:
            board_id: Board containing the section.
            section_id: Section to update.
            version: Expected current version (optimistic lock).
            **fields: Fields to update (now, next, done_recent, status, note, priority).

        Returns:
            The updated section document.

        Raises:
            VersionConflictError: If version doesn't match current document version.
            ValueError: If the section does not exist.
        """
        # Build $set from provided fields
        set_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if hasattr(value, "value"):
                set_fields[key] = value.value
            else:
                set_fields[key] = value
        set_fields["updated_at"] = datetime.now(timezone.utc)

        # Atomic find_one_and_update with version filter
        result = self.sections.find_one_and_update(
            {"board_id": board_id, "section_id": section_id, "version": version},
            {
                "$set": set_fields,
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

        if result is not None:
            result.pop("_id", None)
            return result

        # No match — check if section exists (version mismatch vs missing)
        existing = self.sections.find_one(
            {"board_id": board_id, "section_id": section_id}
        )
        if existing:
            raise VersionConflictError(existing["version"])
        raise ValueError(f"Section '{section_id}' not found on board '{board_id}'")

    def delete_section(self, board_id: str, section_id: str) -> bool:
        """Delete a section and remove it from the parent board."""
        result = self.sections.delete_one(
            {"board_id": board_id, "section_id": section_id}
        )
        if result.deleted_count > 0:
            self.boards.update_one(
                {"board_id": board_id},
                {"$pull": {"section_ids": section_id}},
            )
            logger.info(f"Section deleted: {section_id} from board {board_id}")
            return True
        return False

    # -------------------------------------------------------------------------
    # SNAPSHOT CRUD
    # -------------------------------------------------------------------------

    def create_snapshot(
        self,
        board_id: str,
        snapshot_type: SnapshotType,
        trigger: SnapshotTrigger,
    ) -> dict:
        """
        Create a snapshot capturing current board state (sections + compact_render).

        Returns:
            The inserted snapshot document as a dict.
        """
        # Capture current sections
        current_sections = self.get_sections(board_id)
        sections_data = {
            s["section_id"]: s for s in current_sections
        }

        # Capture compact_render from board
        board = self.get_board(board_id)
        compact_render = board.get("compact_render") if board else None

        # Generate snapshot_id via schema
        create_data = WhiteboardSnapshotCreate(
            board_id=board_id,
            snapshot_type=snapshot_type,
            trigger=trigger,
        )

        now = datetime.now(timezone.utc)
        snapshot = WhiteboardSnapshot(
            snapshot_id=create_data.snapshot_id,
            board_id=board_id,
            snapshot_type=snapshot_type,
            trigger=trigger,
            sections_data=sections_data,
            created_at=now,
        )
        doc = snapshot.model_dump()
        if compact_render:
            doc["compact_render"] = compact_render
        self.snapshots.insert_one(doc)
        logger.info(f"Snapshot created: {create_data.snapshot_id} for board {board_id}")
        doc.pop("_id", None)
        return doc

    def get_snapshot(self, snapshot_id: str) -> dict | None:
        """Get a snapshot by ID. Returns dict or None."""
        doc = self.snapshots.find_one({"snapshot_id": snapshot_id})
        if doc:
            doc.pop("_id", None)
            return doc
        return None

    def list_snapshots(
        self,
        board_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """List snapshots for a board, newest first."""
        cursor = (
            self.snapshots
            .find({"board_id": board_id})
            .sort("created_at", -1)
            .limit(limit)
        )
        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results
