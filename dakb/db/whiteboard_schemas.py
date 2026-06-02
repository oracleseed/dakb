"""
HIVE Whiteboard Schemas

Pydantic v2 models for the Whiteboard feature:
- Boards: project-scoped and global whiteboards
- Sections: agent status, project summary, team goals, recent completions
- Snapshots: periodic/milestone/manual board snapshots

Version: 1.0
Created: 2026-03-18
"""

import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# =============================================================================
# ENUMS
# =============================================================================

class BoardType(str, Enum):
    """Board scope types."""
    PROJECT = "project"
    GLOBAL = "global"


class SectionType(str, Enum):
    """Whiteboard section types."""
    AGENT_STATUS = "agent_status"
    PROJECT_SUMMARY = "project_summary"
    TEAM_GOALS = "team_goals"
    RECENT_COMPLETIONS = "recent_completions"


class SectionStatus(str, Enum):
    """Section operational status."""
    ACTIVE = "active"
    IDLE = "idle"
    STALE = "stale"
    BLOCKED = "blocked"


class SectionPriority(str, Enum):
    """Section priority levels."""
    NORMAL = "normal"
    CRITICAL = "critical"


class SnapshotType(str, Enum):
    """Snapshot types."""
    PERIODIC = "periodic"
    MILESTONE = "milestone"
    MANUAL = "manual"


class SnapshotTrigger(str, Enum):
    """What triggered the snapshot."""
    DAILY_AUTO = "daily_auto"
    PROJECT_COMPLETE = "project_complete"
    USER_REQUEST = "user_request"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _slugify(text: str) -> str:
    """Convert display name to a slug: lowercase, spaces to underscores, strip non-alphanum."""
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9\s_]', '', slug)
    slug = re.sub(r'\s+', '_', slug)
    return slug


# =============================================================================
# EMBEDDED MODELS
# =============================================================================

class BoardSettings(BaseModel):
    """Default settings for a whiteboard."""
    stale_timeout_agent_s: int = Field(default=900, description="Seconds before agent section marked stale")
    stale_timeout_shared_s: int = Field(default=86400, description="Seconds before shared section marked stale")
    snapshot_interval_s: int = Field(default=86400, description="Seconds between auto-snapshots")
    compact_max_chars: int = Field(default=1024, description="Max chars for compact view")
    full_max_chars: int = Field(default=5120, description="Max chars for full view")


# =============================================================================
# BOARD SCHEMAS
# =============================================================================

class WhiteboardBoardCreate(BaseModel):
    """Schema for creating a whiteboard board."""
    board_id: str = Field(default="", description="Auto-generated from display_name; global always 'board_global'")
    display_name: str = Field(..., max_length=100, description="Human-readable board name")
    board_type: BoardType = Field(..., description="Project or global scope")
    project_path: str | None = Field(None, description="Filesystem path for project boards")
    settings: BoardSettings = Field(default_factory=BoardSettings, description="Board settings")

    @model_validator(mode='after')
    def generate_board_id(self):
        if self.board_type == BoardType.GLOBAL:
            self.board_id = "board_global"
        elif not self.board_id or self.board_id == "":
            self.board_id = f"board_{_slugify(self.display_name)}"
        return self


class WhiteboardBoard(BaseModel):
    """Full whiteboard board document."""
    model_config = ConfigDict(populate_by_name=True)

    board_id: str = Field(..., description="Unique board identifier")
    display_name: str = Field(..., max_length=100, description="Human-readable board name")
    board_type: BoardType = Field(..., description="Project or global scope")
    project_path: str | None = Field(None, description="Filesystem path for project boards")
    settings: BoardSettings = Field(default_factory=BoardSettings, description="Board settings")
    compact_render: str | None = Field(None, description="Cached compact markdown render")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    section_ids: list[str] = Field(default_factory=list, description="List of section IDs in this board")


# =============================================================================
# SECTION SCHEMAS
# =============================================================================

class WhiteboardSectionCreate(BaseModel):
    """Schema for creating a whiteboard section."""
    section_id: str = Field(default="", description="Auto-generated based on type + alias/board")
    board_id: str = Field(..., description="Parent board ID")
    section_type: SectionType = Field(..., description="Type of section")
    owner_id: str | None = Field(None, description="Agent/token ID that owns this section")
    owner_alias: str | None = Field(None, description="Agent alias (required for agent_status)")

    # Structured content fields
    now: str | None = Field(None, max_length=500, description="Current task")
    next: str | None = Field(None, max_length=500, description="Next planned task")
    done_recent: list[str] = Field(default_factory=list, description="Recently completed items")
    status: SectionStatus = Field(default=SectionStatus.ACTIVE, description="Section status")
    note: str | None = Field(None, max_length=1000, description="Optional free-text note")
    priority: SectionPriority = Field(default=SectionPriority.NORMAL, description="Section priority")
    version: int = Field(default=1, ge=1, description="Optimistic locking version")

    @model_validator(mode='after')
    def generate_section_id(self):
        if not self.section_id or self.section_id == "":
            type_slug = self.section_type.value
            if self.section_type == SectionType.AGENT_STATUS and self.owner_alias:
                self.section_id = f"sec_{type_slug}_{self.owner_alias.lower()}"
            else:
                self.section_id = f"sec_{type_slug}_{self.board_id}"
        return self


class WhiteboardSectionUpdate(BaseModel):
    """Schema for updating a whiteboard section. Requires version for optimistic locking."""
    version: int = Field(..., ge=1, description="Current version for optimistic locking (required)")
    now: str | None = Field(None, max_length=500, description="Current task")
    next: str | None = Field(None, max_length=500, description="Next planned task")
    done_recent: list[str] | None = Field(None, description="Recently completed items")
    status: SectionStatus | None = Field(None, description="Section status")
    note: str | None = Field(None, max_length=1000, description="Optional free-text note")
    priority: SectionPriority | None = Field(None, description="Section priority")


class WhiteboardSection(BaseModel):
    """Full whiteboard section document."""
    model_config = ConfigDict(populate_by_name=True)

    section_id: str = Field(..., description="Unique section identifier")
    board_id: str = Field(..., description="Parent board ID")
    section_type: SectionType = Field(..., description="Type of section")
    owner_id: str | None = Field(None, description="Agent/token ID that owns this section")
    owner_alias: str | None = Field(None, description="Agent alias")

    # Structured content
    now: str | None = Field(None, max_length=500)
    next: str | None = Field(None, max_length=500)
    done_recent: list[str] = Field(default_factory=list)
    status: SectionStatus = Field(default=SectionStatus.ACTIVE)
    note: str | None = Field(None, max_length=1000)
    priority: SectionPriority = Field(default=SectionPriority.NORMAL)
    version: int = Field(default=1, ge=1)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# SNAPSHOT SCHEMAS
# =============================================================================

class WhiteboardSnapshotCreate(BaseModel):
    """Schema for creating a whiteboard snapshot."""
    snapshot_id: str = Field(default="", description="Auto-generated from timestamp + board")
    board_id: str = Field(..., description="Board being snapshotted")
    snapshot_type: SnapshotType = Field(..., description="Type of snapshot")
    trigger: SnapshotTrigger = Field(..., description="What triggered the snapshot")
    sections_data: dict[str, Any] = Field(default_factory=dict, description="Snapshot of all sections")

    @model_validator(mode='after')
    def generate_snapshot_id(self):
        if not self.snapshot_id or self.snapshot_id == "":
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            unique = uuid.uuid4().hex[:6]
            self.snapshot_id = f"snap_{self.board_id}_{timestamp}_{unique}"
        return self


class WhiteboardSnapshot(BaseModel):
    """Full whiteboard snapshot document."""
    model_config = ConfigDict(populate_by_name=True)

    snapshot_id: str = Field(..., description="Unique snapshot identifier")
    board_id: str = Field(..., description="Board that was snapshotted")
    snapshot_type: SnapshotType = Field(..., description="Type of snapshot")
    trigger: SnapshotTrigger = Field(..., description="What triggered the snapshot")
    sections_data: dict[str, Any] = Field(default_factory=dict, description="Snapshot of all sections")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
