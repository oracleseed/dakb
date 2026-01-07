"""
DAKB Gateway - Session Routes

REST API endpoints for session management including lifecycle tracking,
git context capture, patch bundling, and cross-machine handoff.

Version: 1.1
Created: 2025-12-08
Updated: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Changelog v1.1:
- ISS-075 Fix: Added admin authorization check for all_agents parameter

Endpoints:
- POST /api/v1/sessions - Create a new session
- GET /api/v1/sessions - List sessions
- GET /api/v1/sessions/{session_id} - Get session by ID
- PUT /api/v1/sessions/{session_id} - Update session
- POST /api/v1/sessions/{session_id}/heartbeat - Update activity
- POST /api/v1/sessions/{session_id}/pause - Pause session
- POST /api/v1/sessions/{session_id}/resume - Resume session
- POST /api/v1/sessions/{session_id}/end - End session
- POST /api/v1/sessions/{session_id}/git-context - Capture git context
- POST /api/v1/sessions/{session_id}/patch-bundle - Create patch bundle
- POST /api/v1/sessions/{session_id}/export - Export for handoff
- POST /api/v1/sessions/import - Import from handoff
- GET /api/v1/sessions/handoffs/pending - Get pending handoffs
- POST /api/v1/sessions/handoffs/{handoff_id}/accept - Accept handoff
- POST /api/v1/sessions/handoffs/{handoff_id}/reject - Reject handoff
- GET /api/v1/sessions/stats - Get session statistics
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...db.collections import get_dakb_client
from ...sessions import (
    # Git Context
    GitContextCapture,
    GitContextCaptureError,
    GitContextResponse,
    HandoffAccept,
    HandoffError,
    HandoffRepository,
    HandoffResponse,
    HandoffStatus,
    PatchBundleBuilder,
    PatchBundleError,
    PatchBundleResponse,
    # Models
    SessionCreate,
    # Handoff
    SessionHandoffManager,
    SessionListResponse,
    # Repository
    SessionRepository,
    SessionResponse,
    SessionStats,
    SessionStatus,
    SessionUpdate,
    deserialize_handoff_package,
    find_repository_root,
    serialize_handoff_package,
)
from ..middleware.auth import AuthenticatedAgent, get_current_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["Sessions"])

# ISS-075 Fix: Admin agent types that can use all_agents parameter
ADMIN_AGENT_TYPES = frozenset(["coordinator", "admin", "manager", "system"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class CreateSessionRequest(BaseModel):
    """Request model for creating a new session."""
    working_directory: str = Field(..., description="Working directory path")
    task_description: str | None = Field(None, max_length=500)
    timeout_minutes: int = Field(default=30, ge=1, le=1440)
    loaded_contexts: list[str] = Field(default_factory=list)
    working_files: list[str] = Field(default_factory=list)
    parent_session_id: str | None = Field(None, description="Parent session for continuation")


class UpdateSessionRequest(BaseModel):
    """Request model for updating a session."""
    status: SessionStatus | None = None
    task_description: str | None = Field(None, max_length=500)
    current_step: str | None = None
    working_files: list[str] | None = None
    loaded_contexts: list[str] | None = None
    todo_items: list[str] | None = None
    custom_data: dict | None = None
    knowledge_ids: list[str] | None = None
    timeout_minutes: int | None = Field(None, ge=1, le=1440)


class GitContextCaptureRequest(BaseModel):
    """Request model for capturing git context."""
    repository_path: str | None = Field(
        None,
        description="Path to repository (defaults to session working directory)"
    )
    include_diff_summary: bool = Field(default=True)
    max_diff_size_kb: int = Field(default=100, ge=1, le=1024)


class CreatePatchBundleRequest(BaseModel):
    """Request model for creating a patch bundle."""
    include_stash: bool = Field(default=False, description="Include git stash")
    description: str | None = Field(None, max_length=500)
    compress: bool = Field(default=True)


class ExportSessionRequest(BaseModel):
    """Request model for exporting a session for handoff."""
    target_agent_id: str | None = Field(None, description="Target agent (None = any)")
    target_machine_id: str | None = Field(None, description="Target machine (None = any)")
    include_git_context: bool = Field(default=True)
    include_patch_bundle: bool = Field(default=True)
    include_stash: bool = Field(default=False)
    reason: str | None = Field(None, max_length=500)
    notes: str | None = Field(None, max_length=1000)
    store_on_server: bool = Field(
        default=False,
        description="Store package on server for remote agent retrieval. "
                    "Use GET /handoffs/{handoff_id}/package to fetch."
    )


class ImportSessionRequest(BaseModel):
    """Request model for importing a session from handoff."""
    package_json: str | None = Field(
        None,
        description="JSON-serialized handoff package. Not required if handoff_id is provided."
    )
    handoff_id: str | None = Field(
        None,
        description="Handoff ID to fetch package from server (for remote agents). "
                    "Takes precedence over package_json."
    )
    apply_patch: bool = Field(default=True, description="Apply patch bundle")
    target_directory: str | None = Field(None, description="Override working directory")


class AcceptHandoffRequest(BaseModel):
    """Request model for accepting a handoff."""
    apply_patch: bool = Field(default=True)
    target_directory: str | None = Field(None)


class RejectHandoffRequest(BaseModel):
    """Request model for rejecting a handoff."""
    reason: str | None = Field(None, max_length=500)


class EndSessionRequest(BaseModel):
    """Request model for ending a session."""
    status: SessionStatus = Field(
        default=SessionStatus.COMPLETED,
        description="Final status (COMPLETED or ABANDONED)"
    )


# =============================================================================
# DEPENDENCY HELPERS
# =============================================================================

def get_session_repository() -> SessionRepository:
    """Get session repository instance."""
    client = get_dakb_client()
    return SessionRepository(client.dakb.dakb_sessions)


def get_handoff_repository() -> HandoffRepository:
    """Get handoff repository instance."""
    client = get_dakb_client()
    return HandoffRepository(client.dakb.dakb_sessions)


def get_handoff_manager() -> SessionHandoffManager:
    """Get handoff manager instance."""
    session_repo = get_session_repository()
    handoff_repo = get_handoff_repository()
    return SessionHandoffManager(session_repo, handoff_repo)


# =============================================================================
# SESSION CRUD ENDPOINTS
# =============================================================================

@router.post(
    "",
    response_model=SessionResponse,
    summary="Create a new session",
    description="Create a new session for the authenticated agent."
)
async def create_session(
    request: CreateSessionRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionResponse:
    """
    Create a new session.

    - Sessions track agent work with context preservation
    - Auto-timeout configurable (default 30 minutes)
    - Can continue from a parent session
    """
    repo = get_session_repository()

    try:
        create_data = SessionCreate(
            agent_id=agent.agent_id,
            machine_id=agent.machine_id or "unknown",
            agent_type=agent.agent_type,
            working_directory=request.working_directory,
            task_description=request.task_description,
            timeout_minutes=request.timeout_minutes,
            loaded_contexts=request.loaded_contexts,
            working_files=request.working_files,
            parent_session_id=request.parent_session_id,
        )

        session = repo.create_session(create_data)

        logger.info(
            f"Session {session.session_id} created by {agent.agent_id}"
        )

        return SessionResponse(success=True, session=session)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create session")


@router.get(
    "",
    response_model=SessionListResponse,
    summary="List sessions",
    description="List sessions for the authenticated agent or all sessions."
)
async def list_sessions(
    status: SessionStatus | None = Query(None, description="Filter by status"),
    all_agents: bool = Query(False, description="Include all agents (admin only)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionListResponse:
    """
    List sessions.

    - By default returns sessions for the authenticated agent
    - Admin agents can see all sessions with all_agents=true
    """
    repo = get_session_repository()

    try:
        # ISS-075 Fix: Verify admin privileges for all_agents parameter
        if all_agents and agent.agent_type not in ADMIN_AGENT_TYPES:
            raise HTTPException(
                status_code=403,
                detail="Admin privileges required for all_agents access"
            )

        agent_filter = None if all_agents else agent.agent_id

        if status:
            sessions, total = repo.get_sessions_by_status(
                status=status,
                agent_id=agent_filter,
                page=page,
                page_size=page_size,
            )
        else:
            sessions, total = repo.get_active_sessions(
                agent_id=agent_filter,
                page=page,
                page_size=page_size,
            )

        return SessionListResponse(
            success=True,
            sessions=sessions,
            total=total,
            page=page,
            page_size=page_size,
            has_more=page * page_size < total,
        )

    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(status_code=500, detail="Failed to list sessions")


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Get session by ID",
    description="Get a specific session by its identifier."
)
async def get_session(
    session_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionResponse:
    """Get a session by ID."""
    repo = get_session_repository()

    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionResponse(success=True, session=session)


@router.put(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Update session",
    description="Update session information."
)
async def update_session(
    session_id: str,
    request: UpdateSessionRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionResponse:
    """Update a session."""
    repo = get_session_repository()

    # Check session exists and belongs to agent
    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.agent_id != agent.agent_id:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        update_data = SessionUpdate(
            status=request.status,
            task_description=request.task_description,
            current_step=request.current_step,
            working_files=request.working_files,
            loaded_contexts=request.loaded_contexts,
            todo_items=request.todo_items,
            custom_data=request.custom_data,
            knowledge_ids=request.knowledge_ids,
            timeout_minutes=request.timeout_minutes,
        )

        updated = repo.update_session(session_id, update_data)
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to update session")

        return SessionResponse(success=True, session=updated)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =============================================================================
# SESSION LIFECYCLE ENDPOINTS
# =============================================================================

@router.post(
    "/{session_id}/heartbeat",
    response_model=SessionResponse,
    summary="Update session activity",
    description="Update last activity timestamp (heartbeat)."
)
async def session_heartbeat(
    session_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionResponse:
    """Send heartbeat to keep session active."""
    repo = get_session_repository()

    session = repo.update_activity(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionResponse(success=True, session=session)


@router.post(
    "/{session_id}/pause",
    response_model=SessionResponse,
    summary="Pause session",
    description="Pause an active session."
)
async def pause_session(
    session_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionResponse:
    """Pause an active session."""
    repo = get_session_repository()

    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.agent_id != agent.agent_id:
        raise HTTPException(status_code=403, detail="Access denied")

    paused = repo.pause_session(session_id)
    if not paused:
        raise HTTPException(
            status_code=400,
            detail="Cannot pause session - must be active or resumed"
        )

    return SessionResponse(success=True, session=paused)


@router.post(
    "/{session_id}/resume",
    response_model=SessionResponse,
    summary="Resume session",
    description="Resume a paused session."
)
async def resume_session(
    session_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionResponse:
    """Resume a paused session."""
    repo = get_session_repository()

    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.agent_id != agent.agent_id:
        raise HTTPException(status_code=403, detail="Access denied")

    resumed = repo.resume_session(session_id)
    if not resumed:
        raise HTTPException(
            status_code=400,
            detail="Cannot resume session - must be paused"
        )

    return SessionResponse(success=True, session=resumed)


@router.post(
    "/{session_id}/end",
    response_model=SessionResponse,
    summary="End session",
    description="End an active session."
)
async def end_session(
    session_id: str,
    request: EndSessionRequest = None,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionResponse:
    """End a session."""
    repo = get_session_repository()

    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.agent_id != agent.agent_id:
        raise HTTPException(status_code=403, detail="Access denied")

    status = SessionStatus.COMPLETED
    if request and request.status:
        status = request.status

    ended = repo.end_session(session_id, status)
    if not ended:
        raise HTTPException(
            status_code=400,
            detail="Cannot end session - already completed"
        )

    return SessionResponse(success=True, session=ended)


# =============================================================================
# GIT CONTEXT ENDPOINTS
# =============================================================================

@router.post(
    "/{session_id}/git-context",
    response_model=GitContextResponse,
    summary="Capture git context",
    description="Capture current git repository state for the session."
)
async def capture_git_context(
    session_id: str,
    request: GitContextCaptureRequest = None,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> GitContextResponse:
    """
    Capture git context for a session.

    - Captures branch, commit, uncommitted changes, stash list
    - Saves context to session for handoff support
    """
    repo = get_session_repository()

    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.agent_id != agent.agent_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Determine repository path
    repo_path = request.repository_path if request else None
    if not repo_path:
        repo_path = find_repository_root(session.metadata.working_directory)

    if not repo_path:
        raise HTTPException(
            status_code=400,
            detail="Could not find git repository"
        )

    try:
        import time
        start = time.time()

        capture = GitContextCapture(repo_path)
        git_context = capture.capture()

        capture_time = (time.time() - start) * 1000

        # Save to session
        repo.save_git_context(session_id, git_context)

        return GitContextResponse(
            success=True,
            git_context=git_context,
            capture_time_ms=capture_time,
        )

    except GitContextCaptureError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error capturing git context: {e}")
        raise HTTPException(status_code=500, detail="Failed to capture git context")


@router.get(
    "/{session_id}/git-context",
    response_model=GitContextResponse,
    summary="Get session git context",
    description="Get previously captured git context for a session."
)
async def get_git_context(
    session_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> GitContextResponse:
    """Get the git context for a session."""
    repo = get_session_repository()

    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.git_context:
        raise HTTPException(
            status_code=404,
            detail="No git context captured for this session"
        )

    return GitContextResponse(
        success=True,
        git_context=session.git_context,
    )


# =============================================================================
# PATCH BUNDLE ENDPOINTS
# =============================================================================

@router.post(
    "/{session_id}/patch-bundle",
    response_model=PatchBundleResponse,
    summary="Create patch bundle",
    description="Create a compressed patch bundle for uncommitted changes."
)
async def create_patch_bundle(
    session_id: str,
    request: CreatePatchBundleRequest = None,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> PatchBundleResponse:
    """
    Create a patch bundle for the session.

    - Includes all uncommitted changes (staged, unstaged, untracked)
    - Optionally includes git stash
    - Compressed with gzip (default)
    - Size limit: warn > 1MB, reject > 10MB
    """
    repo = get_session_repository()

    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.agent_id != agent.agent_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Find repository
    repo_path = find_repository_root(session.metadata.working_directory)
    if not repo_path:
        raise HTTPException(
            status_code=400,
            detail="Could not find git repository"
        )

    try:
        include_stash = request.include_stash if request else False
        description = request.description if request else None
        compress = request.compress if request else True

        builder = PatchBundleBuilder(repo_path)
        patch_bundle = builder.create_bundle(
            session_id=session_id,
            agent_id=agent.agent_id,
            machine_id=agent.machine_id or "unknown",
            include_stash=include_stash,
            description=description,
            compress=compress,
        )

        # Save to session
        repo.save_patch_bundle(session_id, patch_bundle)

        warnings = []
        if patch_bundle.is_size_warning():
            warnings.append(
                f"Patch size ({patch_bundle.original_size_bytes / 1024:.1f}KB) "
                f"exceeds 1MB warning threshold"
            )

        return PatchBundleResponse(
            success=True,
            patch_bundle=patch_bundle,
            warnings=warnings,
        )

    except PatchBundleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating patch bundle: {e}")
        raise HTTPException(status_code=500, detail="Failed to create patch bundle")


# =============================================================================
# HANDOFF ENDPOINTS
# =============================================================================

@router.post(
    "/{session_id}/export",
    summary="Export session for handoff",
    description="Export a session with git context and patch bundle for transfer."
)
async def export_session(
    session_id: str,
    request: ExportSessionRequest = None,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> dict:
    """
    Export a session for handoff.

    - Creates a complete handoff package
    - Includes session state, git context, and patch bundle
    - Returns JSON-serialized package for transfer
    """
    manager = get_handoff_manager()

    try:
        req = request or ExportSessionRequest()

        package = manager.export_session(
            session_id=session_id,
            target_agent_id=req.target_agent_id,
            target_machine_id=req.target_machine_id,
            include_git_context=req.include_git_context,
            include_patch_bundle=req.include_patch_bundle,
            include_stash=req.include_stash,
            reason=req.reason,
            notes=req.notes,
        )

        package_json = serialize_handoff_package(package)

        # If store_on_server is requested, save package to MongoDB for remote retrieval
        if req.store_on_server:
            db_client = get_dakb_client()
            packages_collection = db_client.dakb.dakb_handoff_packages
            packages_collection.update_one(
                {"handoff_id": package.handoff_id},
                {
                    "$set": {
                        "handoff_id": package.handoff_id,
                        "package_json": package_json,
                        "package_size_bytes": package.package_size_bytes,
                        "created_at": datetime.utcnow(),
                        "created_by": agent.agent_id,
                        "expires_at": None,  # Could add TTL later
                    }
                },
                upsert=True
            )
            logger.info(
                f"Handoff package {package.handoff_id} stored on server "
                f"({package.package_size_bytes} bytes)"
            )

            return {
                "success": True,
                "handoff_id": package.handoff_id,
                "stored_on_server": True,
                "package_size_bytes": package.package_size_bytes,
                "has_git_context": package.git_context is not None,
                "has_patch_bundle": package.patch_bundle is not None,
                "apply_instructions": package.apply_instructions,
                "conflict_hints": package.conflict_hints,
                "retrieve_url": f"/api/v1/sessions/handoffs/{package.handoff_id}/package",
                "message": (
                    f"Package stored on server. Remote agent can import using handoff_id: "
                    f"'{package.handoff_id}' or fetch via GET {'/api/v1/sessions/handoffs/'}"
                    f"{package.handoff_id}/package"
                ),
            }

        return {
            "success": True,
            "handoff_id": package.handoff_id,
            "package_json": package_json,
            "package_size_bytes": package.package_size_bytes,
            "has_git_context": package.git_context is not None,
            "has_patch_bundle": package.patch_bundle is not None,
            "apply_instructions": package.apply_instructions,
            "conflict_hints": package.conflict_hints,
        }

    except HandoffError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error exporting session: {e}")
        raise HTTPException(status_code=500, detail="Failed to export session")


@router.post(
    "/import",
    response_model=SessionResponse,
    summary="Import session from handoff",
    description="Import a session from a handoff package."
)
async def import_session(
    request: ImportSessionRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionResponse:
    """
    Import a session from a handoff package.

    - Creates a new session continuing from the handoff
    - Optionally applies patch bundle
    - Links to parent session for chain tracking
    - Supports handoff_id for server-stored packages (remote agents)
    """
    manager = get_handoff_manager()

    try:
        package_json = request.package_json

        # If handoff_id is provided, fetch package from server storage
        if request.handoff_id:
            db_client = get_dakb_client()
            packages_collection = db_client.dakb.dakb_handoff_packages
            stored_package = packages_collection.find_one(
                {"handoff_id": request.handoff_id}
            )
            if not stored_package:
                raise HTTPException(
                    status_code=404,
                    detail=f"Handoff package not found on server: {request.handoff_id}"
                )
            package_json = stored_package["package_json"]
            logger.info(
                f"Retrieved handoff package {request.handoff_id} from server "
                f"({stored_package.get('package_size_bytes', 0)} bytes)"
            )

        # Validate we have package_json
        if not package_json:
            raise HTTPException(
                status_code=400,
                detail="Either package_json or handoff_id is required"
            )

        # Deserialize package
        package = deserialize_handoff_package(package_json)

        # Import session
        new_session = manager.import_session(
            package=package,
            agent_id=agent.agent_id,
            machine_id=agent.machine_id or "unknown",
            apply_patch=request.apply_patch,
            target_directory=request.target_directory,
        )

        return SessionResponse(success=True, session=new_session)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid package: {e}")
    except HandoffError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing session: {e}")
        raise HTTPException(status_code=500, detail="Failed to import session")


@router.get(
    "/handoffs/{handoff_id}/package",
    summary="Get stored handoff package",
    description="Retrieve a handoff package stored on the server (for remote agents)."
)
async def get_handoff_package(
    handoff_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> dict:
    """
    Get a handoff package stored on the server.

    Use this endpoint to retrieve packages that were exported with store_on_server=True.
    This is the preferred method for remote agents to avoid MCP response truncation.
    """
    try:
        db_client = get_dakb_client()
        packages_collection = db_client.dakb.dakb_handoff_packages
        stored_package = packages_collection.find_one(
            {"handoff_id": handoff_id}
        )

        if not stored_package:
            raise HTTPException(
                status_code=404,
                detail=f"Handoff package not found: {handoff_id}"
            )

        logger.info(
            f"Agent {agent.agent_id} retrieved handoff package {handoff_id} "
            f"({stored_package.get('package_size_bytes', 0)} bytes)"
        )

        return {
            "success": True,
            "handoff_id": handoff_id,
            "package_json": stored_package["package_json"],
            "package_size_bytes": stored_package.get("package_size_bytes", 0),
            "created_at": stored_package.get("created_at"),
            "created_by": stored_package.get("created_by"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving handoff package: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve handoff package")


@router.get(
    "/handoffs/pending",
    summary="Get pending handoffs",
    description="Get pending handoff requests for the authenticated agent."
)
async def get_pending_handoffs(
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> dict:
    """Get pending handoffs for the current agent."""
    manager = get_handoff_manager()

    try:
        handoffs = manager.get_pending_handoffs_for_agent(
            agent_id=agent.agent_id,
            machine_id=agent.machine_id,
        )

        return {
            "success": True,
            "pending_count": len(handoffs),
            "handoffs": [h.model_dump() for h in handoffs],
        }

    except Exception as e:
        logger.error(f"Error getting pending handoffs: {e}")
        raise HTTPException(status_code=500, detail="Failed to get pending handoffs")


@router.post(
    "/handoffs/{handoff_id}/accept",
    response_model=HandoffResponse,
    summary="Accept handoff",
    description="Accept a pending handoff request."
)
async def accept_handoff(
    handoff_id: str,
    request: AcceptHandoffRequest = None,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> HandoffResponse:
    """Accept a pending handoff."""
    manager = get_handoff_manager()

    try:
        req = request or AcceptHandoffRequest()

        accept = HandoffAccept(
            handoff_id=handoff_id,
            agent_id=agent.agent_id,
            machine_id=agent.machine_id or "unknown",
            apply_patch=req.apply_patch,
            target_directory=req.target_directory,
        )

        success, message, new_session = manager.accept_handoff(accept)

        if not success:
            raise HTTPException(status_code=400, detail=message)

        return HandoffResponse(
            success=True,
            handoff_id=handoff_id,
            status=HandoffStatus.ACCEPTED,
            new_session_id=new_session.session_id if new_session else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error accepting handoff: {e}")
        raise HTTPException(status_code=500, detail="Failed to accept handoff")


@router.post(
    "/handoffs/{handoff_id}/reject",
    response_model=HandoffResponse,
    summary="Reject handoff",
    description="Reject a pending handoff request."
)
async def reject_handoff(
    handoff_id: str,
    request: RejectHandoffRequest = None,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> HandoffResponse:
    """Reject a pending handoff."""
    manager = get_handoff_manager()

    try:
        reason = request.reason if request else None
        success = manager.reject_handoff(handoff_id, reason)

        if not success:
            raise HTTPException(status_code=404, detail="Handoff not found")

        return HandoffResponse(
            success=True,
            handoff_id=handoff_id,
            status=HandoffStatus.REJECTED,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rejecting handoff: {e}")
        raise HTTPException(status_code=500, detail="Failed to reject handoff")


# =============================================================================
# SESSION CHAIN ENDPOINT
# =============================================================================

@router.get(
    "/{session_id}/chain",
    summary="Get session chain",
    description="Get the full session chain (original -> transferred -> ...)."
)
async def get_session_chain(
    session_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> dict:
    """Get the full session chain for handoff tracking."""
    manager = get_handoff_manager()

    try:
        chain = manager.get_session_chain(session_id)

        return {
            "success": True,
            "chain_length": len(chain),
            "sessions": [s.model_dump() for s in chain],
        }

    except Exception as e:
        logger.error(f"Error getting session chain: {e}")
        raise HTTPException(status_code=500, detail="Failed to get session chain")


# =============================================================================
# STATISTICS ENDPOINTS
# =============================================================================

@router.get(
    "/stats",
    response_model=SessionStats,
    summary="Get session statistics",
    description="Get session statistics for the authenticated agent."
)
async def get_stats(
    all_agents: bool = Query(False, description="Include all agents (admin only)"),
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> SessionStats:
    """Get session statistics."""
    repo = get_session_repository()

    try:
        # ISS-075 Fix: Verify admin privileges for all_agents parameter
        if all_agents and agent.agent_type not in ADMIN_AGENT_TYPES:
            raise HTTPException(
                status_code=403,
                detail="Admin privileges required for all_agents access"
            )

        agent_filter = None if all_agents else agent.agent_id
        stats = repo.get_stats(agent_id=agent_filter)
        return stats

    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get statistics")


@router.post(
    "/cleanup-expired",
    summary="Cleanup expired sessions",
    description="Mark expired sessions as abandoned (admin only)."
)
async def cleanup_expired(
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> dict:
    """Cleanup expired sessions."""
    # ISS-075 Fix: Enforce admin-only access
    if agent.agent_type not in ADMIN_AGENT_TYPES:
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required for session cleanup"
        )

    repo = get_session_repository()

    try:
        count = repo.mark_expired_sessions()

        return {
            "success": True,
            "abandoned_count": count,
        }

    except Exception as e:
        logger.error(f"Error cleaning up expired sessions: {e}")
        raise HTTPException(status_code=500, detail="Failed to cleanup")
