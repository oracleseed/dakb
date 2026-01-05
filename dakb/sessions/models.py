"""
DAKB Session Management - Models

Pydantic models for session lifecycle management including session tracking,
git context capture, patch bundling, and cross-machine handoff.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Features:
- Session lifecycle management (ACTIVE, PAUSED, RESUMED, COMPLETED, ABANDONED)
- Git context capture for working tree state
- Patch bundle compression and validation
- Session chain tracking for handoff history
- Configurable auto-timeout (default 30 minutes)
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Any, List
import uuid
import gzip
import base64

from pydantic import BaseModel, Field, field_validator, model_validator


# =============================================================================
# ENUMS
# =============================================================================

class SessionStatus(str, Enum):
    """Session lifecycle states."""
    ACTIVE = "active"           # Currently active and being worked on
    PAUSED = "paused"           # Temporarily paused by user
    RESUMED = "resumed"         # Resumed from pause or handoff
    COMPLETED = "completed"     # Successfully completed
    ABANDONED = "abandoned"     # Timed out or abandoned
    HANDED_OFF = "handed_off"   # Handed off to another agent/machine


class GitChangeType(str, Enum):
    """Types of git file changes."""
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    UNTRACKED = "untracked"


class HandoffStatus(str, Enum):
    """Status of session handoff."""
    PENDING = "pending"         # Handoff initiated, waiting for acceptance
    ACCEPTED = "accepted"       # Handoff accepted by target
    REJECTED = "rejected"       # Handoff rejected by target
    APPLIED = "applied"         # Patch applied successfully
    FAILED = "failed"           # Patch application failed
    CANCELLED = "cancelled"     # Handoff cancelled by source


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def generate_session_id() -> str:
    """Generate a unique session identifier with timestamp prefix."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique = uuid.uuid4().hex[:8]
    return f"sess_{timestamp}_{unique}"


def generate_handoff_id() -> str:
    """Generate a unique handoff identifier."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    unique = uuid.uuid4().hex[:8]
    return f"handoff_{timestamp}_{unique}"


def compress_content(content: str) -> tuple[str, int]:
    """
    Compress content using gzip and return base64-encoded string.

    Args:
        content: String content to compress

    Returns:
        Tuple of (base64-encoded compressed content, original size in bytes)
    """
    original_bytes = content.encode('utf-8')
    original_size = len(original_bytes)
    compressed = gzip.compress(original_bytes)
    encoded = base64.b64encode(compressed).decode('ascii')
    return encoded, original_size


def decompress_content(encoded: str) -> str:
    """
    Decompress base64-encoded gzip content.

    Args:
        encoded: Base64-encoded gzip compressed string

    Returns:
        Decompressed string content
    """
    compressed = base64.b64decode(encoded)
    decompressed = gzip.decompress(compressed)
    return decompressed.decode('utf-8')


# =============================================================================
# GIT CONTEXT MODELS
# =============================================================================

class GitFileChange(BaseModel):
    """Individual file change in git working tree."""
    file_path: str = Field(..., description="Path to changed file")
    change_type: GitChangeType = Field(..., description="Type of change")
    old_path: Optional[str] = Field(None, description="Original path if renamed/copied")
    additions: int = Field(default=0, ge=0, description="Lines added")
    deletions: int = Field(default=0, ge=0, description="Lines deleted")
    is_binary: bool = Field(default=False, description="Whether file is binary")


class GitStashEntry(BaseModel):
    """Git stash entry information."""
    stash_index: int = Field(..., ge=0, description="Stash index (0 = most recent)")
    message: str = Field(..., description="Stash message")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    branch: str = Field(..., description="Branch stash was created from")
    commit_hash: str = Field(..., description="Commit hash when stash was created")


class GitContextSnapshot(BaseModel):
    """
    Complete git context capture for session state.

    Captures current branch, commit, uncommitted changes, and stash list
    for session persistence and handoff.
    """
    # Repository info
    repository_name: str = Field(..., description="Repository name")
    repository_path: str = Field(..., description="Full path to repository root")
    remote_url: Optional[str] = Field(None, description="Git remote URL")

    # Current state
    branch: str = Field(..., description="Current branch name")
    commit_hash: str = Field(..., description="Current HEAD commit hash")
    commit_message: Optional[str] = Field(None, description="Current commit message")
    commit_author: Optional[str] = Field(None, description="Commit author")
    commit_date: Optional[datetime] = Field(None, description="Commit date")

    # Remote tracking
    tracking_branch: Optional[str] = Field(None, description="Upstream tracking branch")
    ahead_count: int = Field(default=0, ge=0, description="Commits ahead of remote")
    behind_count: int = Field(default=0, ge=0, description="Commits behind remote")

    # Working tree state
    has_uncommitted_changes: bool = Field(default=False)
    is_clean: bool = Field(default=True, description="True if working tree is clean")
    staged_changes: List[GitFileChange] = Field(default_factory=list)
    unstaged_changes: List[GitFileChange] = Field(default_factory=list)
    untracked_files: List[str] = Field(default_factory=list)

    # Summary counts
    staged_count: int = Field(default=0, ge=0)
    modified_count: int = Field(default=0, ge=0)
    deleted_count: int = Field(default=0, ge=0)
    untracked_count: int = Field(default=0, ge=0)

    # Stash info
    stash_list: List[GitStashEntry] = Field(default_factory=list)
    has_stash: bool = Field(default=False)

    # Capture metadata
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    capture_duration_ms: Optional[float] = Field(None, description="Time to capture context")

    @model_validator(mode='after')
    def update_summary_counts(self) -> 'GitContextSnapshot':
        """Update summary counts from changes lists."""
        self.staged_count = len(self.staged_changes)
        self.modified_count = len([c for c in self.unstaged_changes if c.change_type == GitChangeType.MODIFIED])
        self.deleted_count = len([c for c in self.unstaged_changes if c.change_type == GitChangeType.DELETED])
        self.untracked_count = len(self.untracked_files)
        self.has_stash = len(self.stash_list) > 0
        self.has_uncommitted_changes = (
            self.staged_count > 0 or
            len(self.unstaged_changes) > 0 or
            self.untracked_count > 0
        )
        self.is_clean = not self.has_uncommitted_changes
        return self


# =============================================================================
# PATCH BUNDLE MODELS
# =============================================================================

class PatchBundle(BaseModel):
    """
    Compressed patch bundle for git changes.

    Contains unified diff of all uncommitted changes, optionally including
    stash contents. Supports compression and size validation.
    """
    bundle_id: str = Field(
        default_factory=lambda: f"patch_{uuid.uuid4().hex[:8]}",
        description="Unique bundle identifier"
    )

    # Source info
    source_session_id: str = Field(..., description="Session this patch is from")
    source_agent_id: str = Field(..., description="Agent that created the patch")
    source_machine_id: str = Field(..., description="Machine the patch was created on")

    # Git base state
    base_branch: str = Field(..., description="Branch patch was created against")
    base_commit: str = Field(..., description="Commit hash patch was created against")

    # Patch content (compressed)
    patch_content: str = Field(
        ...,
        description="Base64-encoded gzip compressed unified diff"
    )
    patch_format: str = Field(
        default="unified_diff",
        description="Patch format (unified_diff, git_format_patch)"
    )
    is_compressed: bool = Field(default=True)

    # Size tracking
    original_size_bytes: int = Field(..., ge=0, description="Original uncompressed size")
    compressed_size_bytes: int = Field(..., ge=0, description="Compressed size")
    compression_ratio: float = Field(default=1.0, ge=0.0, description="Compression ratio")

    # File summary
    files_count: int = Field(default=0, ge=0, description="Number of files in patch")
    files_changed: List[str] = Field(default_factory=list, description="List of changed files")
    additions_total: int = Field(default=0, ge=0, description="Total lines added")
    deletions_total: int = Field(default=0, ge=0, description="Total lines deleted")

    # Stash inclusion
    includes_stash: bool = Field(default=False, description="Whether stash is included")
    stash_entries_count: int = Field(default=0, ge=0)

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    description: Optional[str] = Field(None, max_length=500, description="Patch description")
    author: Optional[str] = Field(None, description="Patch author")

    # Validation
    is_valid: bool = Field(default=True, description="Whether patch passed validation")
    validation_warnings: List[str] = Field(default_factory=list)
    can_apply_cleanly: Optional[bool] = Field(None, description="Whether patch can apply cleanly")

    @field_validator('original_size_bytes')
    @classmethod
    def validate_size_warning(cls, v: int) -> int:
        """Warn if patch size exceeds 1MB."""
        if v > 1024 * 1024:  # 1MB
            # This is just a warning, not a rejection
            pass
        return v

    @field_validator('original_size_bytes')
    @classmethod
    def validate_size_limit(cls, v: int) -> int:
        """Reject if patch size exceeds 10MB."""
        if v > 10 * 1024 * 1024:  # 10MB limit
            raise ValueError("Patch size exceeds 10MB limit")
        return v

    def get_decompressed_patch(self) -> str:
        """Get decompressed patch content."""
        if self.is_compressed:
            return decompress_content(self.patch_content)
        return self.patch_content

    def is_size_warning(self) -> bool:
        """Check if patch size triggers a warning."""
        return self.original_size_bytes > 1024 * 1024  # > 1MB


# =============================================================================
# SESSION MODELS
# =============================================================================

class SessionMetadata(BaseModel):
    """Session metadata for context preservation."""
    working_directory: str = Field(..., description="Current working directory")
    task_description: Optional[str] = Field(None, max_length=500, description="Task being worked on")
    loaded_contexts: List[str] = Field(default_factory=list, description="Loaded context files")
    working_files: List[str] = Field(default_factory=list, description="Files being edited")
    current_step: Optional[str] = Field(None, description="Current step in task")
    todo_items: List[str] = Field(default_factory=list, description="Pending todo items")
    environment_vars: dict[str, str] = Field(default_factory=dict, description="Relevant env vars")
    custom_data: dict[str, Any] = Field(default_factory=dict, description="Agent-specific data")


class SessionChainEntry(BaseModel):
    """Entry in session handoff chain."""
    session_id: str = Field(..., description="Session identifier")
    agent_id: str = Field(..., description="Agent for this session")
    machine_id: str = Field(..., description="Machine for this session")
    status: SessionStatus = Field(..., description="Status when handed off")
    started_at: datetime = Field(..., description="When session started")
    ended_at: Optional[datetime] = Field(None, description="When session ended/handed off")
    handoff_notes: Optional[str] = Field(None, description="Notes from handoff")


class Session(BaseModel):
    """
    Core session model for agent session tracking.

    Tracks session lifecycle with support for context snapshots,
    git state capture, and cross-machine handoff.
    """
    session_id: str = Field(
        default_factory=generate_session_id,
        description="Unique session identifier"
    )

    # Agent/Machine identity
    agent_id: str = Field(..., description="Agent running the session")
    machine_id: str = Field(..., description="Machine running the session")
    agent_type: Optional[str] = Field(None, description="Type of agent (claude, gpt, etc.)")

    # Session state
    status: SessionStatus = Field(
        default=SessionStatus.ACTIVE,
        description="Current session status"
    )

    # Timing
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_active_at: datetime = Field(default_factory=datetime.utcnow)
    paused_at: Optional[datetime] = Field(None, description="When session was paused")
    resumed_at: Optional[datetime] = Field(None, description="When session was resumed")
    ended_at: Optional[datetime] = Field(None, description="When session ended")

    # Timeout configuration (minutes)
    timeout_minutes: int = Field(default=30, ge=1, le=1440, description="Auto-timeout in minutes")

    # Context
    metadata: SessionMetadata = Field(
        default_factory=SessionMetadata,
        description="Session metadata"
    )

    # Git context (optional)
    git_context: Optional[GitContextSnapshot] = Field(
        None,
        description="Git state at last capture"
    )
    git_context_captured_at: Optional[datetime] = Field(None)

    # Patch bundle (optional, for handoff)
    patch_bundle: Optional[PatchBundle] = Field(
        None,
        description="Patch bundle for changes"
    )

    # Session chain (handoff history)
    session_chain: List[SessionChainEntry] = Field(
        default_factory=list,
        description="Chain of sessions (original -> transferred -> ...)"
    )
    original_session_id: Optional[str] = Field(
        None,
        description="Original session ID if this is a handoff continuation"
    )
    parent_session_id: Optional[str] = Field(
        None,
        description="Immediate parent session ID"
    )

    # Handoff tracking
    handed_off_to_agent: Optional[str] = Field(None, description="Agent session was handed to")
    handed_off_to_machine: Optional[str] = Field(None, description="Machine session was handed to")
    handoff_timestamp: Optional[datetime] = Field(None)
    handoff_notes: Optional[str] = Field(None, max_length=1000)

    # Knowledge generated
    knowledge_ids: List[str] = Field(
        default_factory=list,
        description="Knowledge entries created in this session"
    )

    # Statistics
    total_active_time_seconds: int = Field(
        default=0,
        ge=0,
        description="Total time session was active"
    )
    pause_count: int = Field(default=0, ge=0, description="Number of times paused")
    handoff_count: int = Field(default=0, ge=0, description="Number of times handed off")

    def is_expired(self) -> bool:
        """Check if session has timed out."""
        timeout_delta = timedelta(minutes=self.timeout_minutes)
        return datetime.utcnow() > (self.last_active_at + timeout_delta)

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_active_at = datetime.utcnow()

    def calculate_active_time(self) -> int:
        """Calculate total active time in seconds."""
        if self.ended_at:
            end = self.ended_at
        else:
            end = datetime.utcnow()

        if self.started_at:
            delta = end - self.started_at
            return int(delta.total_seconds())
        return 0

    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "sess_20251208_abc12345",
                "agent_id": "backend",
                "machine_id": "mac-m3-local",
                "status": "active",
                "metadata": {
                    "working_directory": "/Users/dev/dakb",
                    "task_description": "Implementing DAKB session management",
                    "working_files": ["sessions/models.py"]
                },
                "timeout_minutes": 30
            }
        }


# =============================================================================
# HANDOFF MODELS
# =============================================================================

class HandoffRequest(BaseModel):
    """Request to handoff a session to another agent/machine."""
    handoff_id: str = Field(
        default_factory=generate_handoff_id,
        description="Unique handoff identifier"
    )

    # Source
    source_session_id: str = Field(..., description="Session being handed off")
    source_agent_id: str = Field(..., description="Agent initiating handoff")
    source_machine_id: str = Field(..., description="Machine initiating handoff")

    # Target
    target_agent_id: Optional[str] = Field(None, description="Target agent (None = any)")
    target_machine_id: Optional[str] = Field(None, description="Target machine (None = any)")

    # Content
    include_git_context: bool = Field(default=True, description="Include git context")
    include_patch_bundle: bool = Field(default=True, description="Include patch bundle")
    include_stash: bool = Field(default=False, description="Include stash in patch")

    # Handoff metadata
    reason: Optional[str] = Field(None, max_length=500, description="Reason for handoff")
    notes: Optional[str] = Field(None, max_length=1000, description="Notes for target")
    priority: str = Field(default="normal", description="Handoff priority")

    # Status
    status: HandoffStatus = Field(default=HandoffStatus.PENDING)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(
        default_factory=lambda: datetime.utcnow() + timedelta(hours=24),
        description="Handoff expires after 24 hours"
    )
    accepted_at: Optional[datetime] = Field(None)
    applied_at: Optional[datetime] = Field(None)

    # Result
    result_session_id: Optional[str] = Field(
        None,
        description="New session ID if handoff succeeded"
    )
    error_message: Optional[str] = Field(None, description="Error if handoff failed")


class HandoffPackage(BaseModel):
    """
    Complete package for session handoff.

    Contains everything needed to restore session on another machine.
    """
    handoff_id: str = Field(..., description="Handoff identifier")

    # Session snapshot
    session: Session = Field(..., description="Session to transfer")

    # Git context (optional)
    git_context: Optional[GitContextSnapshot] = Field(
        None,
        description="Git state snapshot"
    )

    # Patch bundle (optional)
    patch_bundle: Optional[PatchBundle] = Field(
        None,
        description="Patch bundle for changes"
    )

    # Application instructions
    apply_instructions: List[str] = Field(
        default_factory=list,
        description="Steps to apply handoff"
    )
    conflict_hints: List[str] = Field(
        default_factory=list,
        description="Hints for resolving conflicts"
    )

    # Validation
    package_size_bytes: int = Field(default=0, ge=0)
    is_valid: bool = Field(default=True)
    validation_errors: List[str] = Field(default_factory=list)

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = Field(..., description="Agent that created package")


# =============================================================================
# CREATE/UPDATE MODELS
# =============================================================================

class SessionCreate(BaseModel):
    """Schema for creating new sessions."""
    agent_id: str = Field(..., description="Agent identifier")
    machine_id: str = Field(..., description="Machine identifier")
    agent_type: Optional[str] = Field(None, description="Agent type")

    # Initial metadata
    working_directory: str = Field(..., description="Working directory path")
    task_description: Optional[str] = Field(None, max_length=500)
    timeout_minutes: int = Field(default=30, ge=1, le=1440)

    # Optional initial context
    loaded_contexts: List[str] = Field(default_factory=list)
    working_files: List[str] = Field(default_factory=list)

    # Optional parent session (for continuation)
    parent_session_id: Optional[str] = Field(None)


class SessionUpdate(BaseModel):
    """Schema for updating session information."""
    status: Optional[SessionStatus] = Field(None)
    task_description: Optional[str] = Field(None, max_length=500)
    current_step: Optional[str] = Field(None)
    working_files: Optional[List[str]] = Field(None)
    loaded_contexts: Optional[List[str]] = Field(None)
    todo_items: Optional[List[str]] = Field(None)
    custom_data: Optional[dict[str, Any]] = Field(None)
    knowledge_ids: Optional[List[str]] = Field(None)
    timeout_minutes: Optional[int] = Field(None, ge=1, le=1440)


class GitContextRequest(BaseModel):
    """Request to capture git context for a session."""
    session_id: str = Field(..., description="Session to capture for")
    repository_path: str = Field(..., description="Path to repository")
    include_diff_summary: bool = Field(default=True)
    max_diff_size_kb: int = Field(default=100, ge=1, le=1024)


class PatchBundleCreate(BaseModel):
    """Request to create a patch bundle."""
    session_id: str = Field(..., description="Session to create patch for")
    include_stash: bool = Field(default=False)
    description: Optional[str] = Field(None, max_length=500)
    compress: bool = Field(default=True)


class HandoffCreate(BaseModel):
    """Request to initiate session handoff."""
    source_session_id: str = Field(..., description="Session to hand off")
    target_agent_id: Optional[str] = Field(None, description="Target agent")
    target_machine_id: Optional[str] = Field(None, description="Target machine")
    reason: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=1000)
    include_git_context: bool = Field(default=True)
    include_patch_bundle: bool = Field(default=True)
    include_stash: bool = Field(default=False)


class HandoffAccept(BaseModel):
    """Request to accept a session handoff."""
    handoff_id: str = Field(..., description="Handoff to accept")
    agent_id: str = Field(..., description="Accepting agent")
    machine_id: str = Field(..., description="Accepting machine")
    apply_patch: bool = Field(default=True, description="Whether to apply patch")
    target_directory: Optional[str] = Field(None, description="Override target directory")


# =============================================================================
# RESPONSE MODELS
# =============================================================================

class SessionResponse(BaseModel):
    """Response wrapper for single session."""
    success: bool = Field(default=True)
    session: Optional[Session] = Field(None)
    error: Optional[str] = Field(None)


class SessionListResponse(BaseModel):
    """Response wrapper for session list queries."""
    success: bool = Field(default=True)
    sessions: List[Session] = Field(default_factory=list)
    total: int = Field(default=0)
    page: int = Field(default=1)
    page_size: int = Field(default=20)
    has_more: bool = Field(default=False)
    error: Optional[str] = Field(None)


class GitContextResponse(BaseModel):
    """Response for git context capture."""
    success: bool = Field(default=True)
    git_context: Optional[GitContextSnapshot] = Field(None)
    capture_time_ms: Optional[float] = Field(None)
    error: Optional[str] = Field(None)


class PatchBundleResponse(BaseModel):
    """Response for patch bundle creation."""
    success: bool = Field(default=True)
    patch_bundle: Optional[PatchBundle] = Field(None)
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = Field(None)


class HandoffResponse(BaseModel):
    """Response for handoff operations."""
    success: bool = Field(default=True)
    handoff_id: Optional[str] = Field(None)
    status: Optional[HandoffStatus] = Field(None)
    new_session_id: Optional[str] = Field(None, description="New session if applied")
    error: Optional[str] = Field(None)


class SessionStats(BaseModel):
    """Statistics for sessions."""
    total_sessions: int = Field(default=0, ge=0)
    active_sessions: int = Field(default=0, ge=0)
    paused_sessions: int = Field(default=0, ge=0)
    completed_sessions: int = Field(default=0, ge=0)
    abandoned_sessions: int = Field(default=0, ge=0)
    total_handoffs: int = Field(default=0, ge=0)
    by_agent: dict[str, int] = Field(default_factory=dict)
    by_machine: dict[str, int] = Field(default_factory=dict)
