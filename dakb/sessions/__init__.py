"""
DAKB Session Management Module

Provides session lifecycle management, git context capture, patch bundling,
and cross-machine session handoff capabilities.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Components:
- models: Pydantic models for sessions, git context, patch bundles, handoffs
- repository: MongoDB CRUD operations for sessions
- git_context: Git repository state capture
- patch_bundle: Compressed diff bundle creation and application
- handoff: Cross-machine session transfer protocol

Usage:
    from dakb_service.sessions import (
        Session,
        SessionCreate,
        SessionRepository,
        GitContextCapture,
        PatchBundleBuilder,
        SessionHandoffManager,
    )
"""

# Models
# Git context capture
from .git_context import (
    GitContextCapture,
    GitContextCaptureError,
    capture_git_context,
    find_repository_root,
    is_git_repository,
)

# Handoff operations
from .handoff import (
    HandoffError,
    SessionHandoffManager,
    deserialize_handoff_package,
    export_package_to_file,
    import_package_from_file,
    serialize_handoff_package,
)
from .models import (
    GitChangeType,
    GitContextRequest,
    GitContextResponse,
    GitContextSnapshot,
    # Git context models
    GitFileChange,
    GitStashEntry,
    HandoffAccept,
    HandoffCreate,
    HandoffPackage,
    # Handoff models
    HandoffRequest,
    HandoffResponse,
    HandoffStatus,
    # Patch bundle models
    PatchBundle,
    PatchBundleCreate,
    PatchBundleResponse,
    Session,
    SessionChainEntry,
    # Create/Update schemas
    SessionCreate,
    SessionListResponse,
    # Session models
    SessionMetadata,
    # Response models
    SessionResponse,
    SessionStats,
    # Enums
    SessionStatus,
    SessionUpdate,
    compress_content,
    decompress_content,
    generate_handoff_id,
    # Helpers
    generate_session_id,
)

# Patch bundle operations
from .patch_bundle import (
    SIZE_LIMIT_BYTES,
    SIZE_WARNING_BYTES,
    PatchBundleApplier,
    PatchBundleBuilder,
    PatchBundleError,
    apply_patch_bundle,
    create_patch_bundle,
)

# Repository
from .repository import (
    HandoffRepository,
    SessionRepository,
)

__all__ = [
    # Enums
    "SessionStatus",
    "GitChangeType",
    "HandoffStatus",
    # Git context models
    "GitFileChange",
    "GitStashEntry",
    "GitContextSnapshot",
    # Patch bundle models
    "PatchBundle",
    # Session models
    "SessionMetadata",
    "SessionChainEntry",
    "Session",
    # Handoff models
    "HandoffRequest",
    "HandoffPackage",
    # Create/Update schemas
    "SessionCreate",
    "SessionUpdate",
    "GitContextRequest",
    "PatchBundleCreate",
    "HandoffCreate",
    "HandoffAccept",
    # Response models
    "SessionResponse",
    "SessionListResponse",
    "GitContextResponse",
    "PatchBundleResponse",
    "HandoffResponse",
    "SessionStats",
    # Helpers
    "generate_session_id",
    "generate_handoff_id",
    "compress_content",
    "decompress_content",
    # Repository
    "SessionRepository",
    "HandoffRepository",
    # Git context
    "GitContextCapture",
    "GitContextCaptureError",
    "capture_git_context",
    "is_git_repository",
    "find_repository_root",
    # Patch bundle
    "PatchBundleBuilder",
    "PatchBundleApplier",
    "PatchBundleError",
    "create_patch_bundle",
    "apply_patch_bundle",
    "SIZE_WARNING_BYTES",
    "SIZE_LIMIT_BYTES",
    # Handoff
    "SessionHandoffManager",
    "HandoffError",
    "serialize_handoff_package",
    "deserialize_handoff_package",
    "export_package_to_file",
    "import_package_from_file",
]
