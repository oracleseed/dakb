"""
HIVE Whiteboard REST API Routes

Endpoints for whiteboard boards, sections, and snapshots.

Endpoints:
- GET    /whiteboard/boards                              - List all boards
- GET    /whiteboard/boards/{board_id}                   - Get board with sections (full)
- GET    /whiteboard/boards/{board_id}/compact            - Get compact render (0-1K)
- GET    /whiteboard/boards/{board_id}/render             - Dashboard-formatted JSON
- POST   /whiteboard/boards                              - Create a board
- POST   /whiteboard/sections                            - Create a new section
- PATCH  /whiteboard/sections/{board_id}/{section_id}    - Update section (version required, 409 on conflict)
- DELETE /whiteboard/sections/{board_id}/{section_id}    - Delete a section
- POST   /whiteboard/boards/{board_id}/snapshot          - Create a snapshot
- GET    /whiteboard/boards/{board_id}/snapshots          - List snapshots
- GET    /whiteboard/snapshots/{snapshot_id}              - Get a single snapshot

Auto-lifecycle triggers (Phase 2):
- session_start → agent section set to active + NOW = task_description
- session_end → agent set to idle, NOW moved to done_recent
- heartbeat → refreshes stale timeout

Version: 1.0
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...db.schemas import AgentRole
from ...db.whiteboard_repository import VersionConflictError, WhiteboardRepository
from ...db.whiteboard_schemas import (
    BoardType,
    SnapshotTrigger,
    SnapshotType,
)
from ..agentic import ok_response, raise_issue
from ..middleware.auth import (
    AuthenticatedAgent,
    check_rate_limit,
    get_current_agent,
)
from ..whiteboard_renderer import render_compact, render_full

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/whiteboard",
    tags=["Whiteboard"],
    dependencies=[Depends(check_rate_limit)],  # auth on ALL routes (check_rate_limit -> get_current_agent)
)

# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================

_repo: WhiteboardRepository | None = None


def set_repository(repo: WhiteboardRepository) -> None:
    """Inject the WhiteboardRepository instance (called at app startup)."""
    global _repo
    _repo = repo


def _get_repo() -> WhiteboardRepository:
    """Get the injected repository. Raises if not set."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Whiteboard service not initialized")
    return _repo


# =============================================================================
# REQUEST MODELS
# =============================================================================

class CreateBoardRequest(BaseModel):
    board_type: str = Field(..., description="'project' or 'global'")
    display_name: str = Field(..., max_length=100)
    project_path: str | None = Field(None)


class UpdateSectionRequest(BaseModel):
    version: int = Field(..., ge=1, description="Current version for optimistic locking")
    now: str | None = Field(None, max_length=500)
    next: str | None = Field(None, max_length=500)
    done_recent: list[str] | None = Field(None)
    status: str | None = Field(None)
    note: str | None = Field(None, max_length=1000)
    priority: str | None = Field(None)


class CreateSectionRequest(BaseModel):
    board_id: str = Field(..., description="Board to create section on")
    section_type: str = Field(..., description="'agent_status', 'project_summary', 'team_goals', or 'recent_completions'")
    owner_alias: str = Field(..., max_length=100, description="Display name / alias")
    now: str | None = Field(None, max_length=500)
    next: str | None = Field(None, max_length=500)
    done_recent: list[str] | None = Field(None)
    note: str | None = Field(None, max_length=1000)


class CreateSnapshotRequest(BaseModel):
    snapshot_type: str = Field(..., description="'periodic', 'milestone', or 'manual'")
    trigger: str = Field(..., description="'daily_auto', 'project_complete', or 'user_request'")


# =============================================================================
# COMPACT RE-RENDER HELPER
# =============================================================================

def _re_render_compact(repo: WhiteboardRepository, board_id: str) -> None:
    """Re-render and cache compact view after section changes."""
    try:
        board = repo.get_board(board_id)
        if board:
            sections = repo.get_sections(board_id)
            compact = render_compact(board, sections)
            repo.update_compact_render(board_id, compact)
    except Exception as e:
        logger.warning(f"Failed to re-render compact for {board_id}: {e}")


# =============================================================================
# BOARD ENDPOINTS
# =============================================================================

@router.get("/boards")
async def list_boards(board_type: str | None = Query(None)):
    """List all whiteboard boards, optionally filtered by type."""
    repo = _get_repo()
    boards = repo.list_boards(board_type=board_type)
    return ok_response(
        data={"boards": boards, "total": len(boards)},
        actions=["get_boards", "get_board", "create_section"],
    )


@router.get("/boards/{board_id}")
async def get_board(board_id: str):
    """Get a board with all its sections and full rendered view."""
    repo = _get_repo()
    board = repo.get_board(board_id)
    if not board:
        raise_issue(
            "WB.BOARD_NOT_FOUND",
            status=404,
            message=f"Board '{board_id}' not found",
            context={"board_id": board_id},
        )
    sections = repo.get_sections(board_id)
    full_render = render_full(board, sections)
    return ok_response(
        data={"board": board, "sections": sections, "full_render": full_render},
        actions=["get_boards", "get_board", "update_section", "create_section"],
    )


@router.get("/boards/{board_id}/compact")
async def get_board_compact(board_id: str):
    """Get the compact render for a board."""
    repo = _get_repo()
    board = repo.get_board(board_id)
    if not board:
        raise_issue(
            "WB.BOARD_NOT_FOUND",
            status=404,
            message=f"Board '{board_id}' not found",
            context={"board_id": board_id},
        )

    # Return cached compact_render if available, otherwise render fresh
    compact = board.get("compact_render")
    if not compact:
        sections = repo.get_sections(board_id)
        compact = render_compact(board, sections)
        repo.update_compact_render(board_id, compact)

    return ok_response(
        data={"board_id": board_id, "compact_render": compact},
        actions=["get_boards", "get_board", "update_section"],
    )


@router.post("/boards", status_code=201)
async def create_board(request: CreateBoardRequest):
    """Create a new whiteboard board."""
    repo = _get_repo()
    try:
        board = repo.create_board(
            board_type=BoardType(request.board_type),
            display_name=request.display_name,
            project_path=request.project_path,
        )
        return ok_response(
            data=board,
            http_status=201,
            actions=["get_boards", "get_board", "create_section"],
            suggestions=[
                f"View this board: GET /api/v1/whiteboard/boards/{board.get('board_id', '')}",
                "Add a section: POST /api/v1/whiteboard/sections",
            ],
        )
    except Exception as e:
        if "DuplicateKeyError" in type(e).__name__ or "duplicate" in str(e).lower():
            raise_issue(
                "WB.BOARD_EXISTS",
                status=409,
                message=f"Board with display_name '{request.display_name}' already exists",
                context={"display_name": request.display_name, "board_type": request.board_type},
            )
        raise


# =============================================================================
# DASHBOARD RENDER ENDPOINT
# =============================================================================

@router.get("/boards/{board_id}/render")
async def get_board_render(board_id: str):
    """
    Get board data formatted for the admin dashboard panel.
    Transforms sections into projects/agents/goals/completions structure.
    No auth required (admin dashboard is already auth-gated).
    """
    repo = _get_repo()
    board = repo.get_board(board_id)
    if not board:
        raise_issue(
            "WB.BOARD_NOT_FOUND",
            status=404,
            message=f"Board '{board_id}' not found",
            context={"board_id": board_id},
        )

    sections = repo.get_sections(board_id)
    all_boards = repo.list_boards()

    # Transform sections into dashboard-expected format
    agents = []
    goals = []
    completions = []
    projects = []

    for s in sections:
        stype = s.get("section_type", "")

        if stype == "agent_status":
            status = s.get("status", "idle")
            presence_map = {"active": "active", "idle": "idle", "stale": "stale", "blocked": "blocked"}
            done_items = s.get("done_recent", [])
            agents.append({
                "alias": s.get("owner_alias", "Unknown"),
                "agent_id": s.get("owner_id", ""),
                "presence": presence_map.get(status, "idle"),
                "project": board.get("display_name", ""),
                "now": s.get("now") or "Idle",
                "next": s.get("next") or "-",
                "done": done_items[0] if done_items else "-",
                "last_seen": s.get("updated_at", "").isoformat() if hasattr(s.get("updated_at", ""), "isoformat") else str(s.get("updated_at", "")),
                "is_blocked": s.get("is_blocked", False),
                "priority": s.get("priority", "normal"),
            })

        elif stype == "project_summary":
            # Count agents on this board
            agent_count = sum(1 for sec in sections if sec.get("section_type") == "agent_status")
            projects.append({
                "name": board.get("display_name", s.get("owner_alias", "")),
                "status": s.get("status", "active") if s.get("now") else "idle",
                "agent_count": agent_count,
                "current_task": s.get("now", "-"),
            })

        elif stype == "team_goals":
            done_items = s.get("done_recent", [])
            note = s.get("note", "")
            # Parse open goals from done_recent (they're the goal list)
            for item in done_items:
                goals.append({"text": item, "done": False})
            # Parse completed goals from note (prefixed with "Completed:")
            if note and "completed:" in note.lower():
                completed_text = note.split(":", 1)[1].strip() if ":" in note else note
                for item in completed_text.split(","):
                    item = item.strip()
                    if item:
                        goals.append({"text": item, "done": True})

        elif stype == "recent_completions":
            for item in s.get("done_recent", []):
                # Parse "2026-01-01: description — agent" format
                parts = item.split(":", 1) if ":" in item else ["", item]
                date_part = parts[0].strip() if len(parts) > 1 else ""
                rest = parts[1].strip() if len(parts) > 1 else item
                # Split on " — " for agent
                if " — " in rest:
                    desc, agent = rest.rsplit(" — ", 1)
                elif " - " in rest:
                    desc, agent = rest.rsplit(" - ", 1)
                else:
                    desc, agent = rest, ""
                completions.append({
                    "date": date_part,
                    "description": desc.strip(),
                    "agent": agent.strip(),
                })

    # For global board, build projects from all project boards
    if board.get("board_type") == "global":
        projects = []
        for b in all_boards:
            if b.get("board_type") == "project":
                b_sections = repo.get_sections(b["board_id"])
                agent_count = sum(1 for s in b_sections if s.get("section_type") == "agent_status")
                summary = next((s for s in b_sections if s.get("section_type") == "project_summary"), None)
                projects.append({
                    "name": b.get("display_name", b["board_id"]),
                    "status": "active" if summary and summary.get("now") else "idle",
                    "agent_count": agent_count,
                    "current_task": summary.get("now", "-") if summary else "-",
                })

    return ok_response(
        data={
            "board_id": board_id,
            "board_type": board.get("board_type", "project"),
            "display_name": board.get("display_name", ""),
            "projects": projects,
            "agents": agents,
            "goals": goals,
            "completions": completions,
            "boards": [{"board_id": b["board_id"], "name": b.get("display_name", b["board_id"])} for b in all_boards if b.get("board_type") == "project"],
        },
        actions=["get_boards", "get_board", "update_section", "create_section"],
    )


# =============================================================================
# SECTION ENDPOINTS
# =============================================================================

def _require_owner_or_admin(
    repo: WhiteboardRepository,
    board_id: str,
    section_id: str,
    agent: AuthenticatedAgent,
) -> dict:
    """Fetch the section and ensure the caller owns it (or is an admin).

    Returns the section dict on success. Raises WB.SECTION_NOT_FOUND (404)
    if the section does not exist, or WB.FORBIDDEN (403) if the caller is
    neither the owner nor an admin.
    """
    section = repo.get_section(board_id, section_id)
    if not section:
        raise_issue(
            "WB.SECTION_NOT_FOUND",
            status=404,
            message=f"Section '{section_id}' not found on board '{board_id}'",
            context={"board_id": board_id, "section_id": section_id},
        )
    is_owner = section.get("owner_id") == agent.agent_id
    is_admin = agent.role == AgentRole.ADMIN
    if not (is_owner or is_admin):
        raise_issue(
            "WB.FORBIDDEN",
            status=403,
            message=(
                f"Agent '{agent.agent_id}' may not modify section "
                f"'{section_id}' owned by '{section.get('owner_id')}'"
            ),
            context={
                "board_id": board_id,
                "section_id": section_id,
                "owner_id": section.get("owner_id"),
                "agent_id": agent.agent_id,
            },
        )
    return section


@router.post("/sections", status_code=201)
async def create_section(
    request: CreateSectionRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """
    Create a new section on a board.
    owner_id is derived from the authenticated agent — it cannot be spoofed
    via the request body.
    """
    repo = _get_repo()
    board = repo.get_board(request.board_id)
    if not board:
        raise_issue(
            "WB.BOARD_NOT_FOUND",
            status=404,
            message=f"Board '{request.board_id}' not found — cannot create section on nonexistent board",
            context={"board_id": request.board_id},
        )
    try:
        section = repo.create_section(
            board_id=request.board_id,
            section_type=request.section_type,
            owner_id=agent.agent_id,          # <-- from token, was request.owner_id
            owner_alias=request.owner_alias,
            now=request.now,
            next=request.next,
            done_recent=request.done_recent or [],
            note=request.note or "",
        )
        _re_render_compact(repo, request.board_id)
        return ok_response(
            data=section,
            http_status=201,
            actions=["get_board", "update_section", "create_section"],
            suggestions=[
                f"View board: GET /api/v1/whiteboard/boards/{request.board_id}",
                f"Update this section: PATCH /api/v1/whiteboard/sections/{request.board_id}/{section.get('section_id', '')}",
            ],
        )
    except Exception as e:
        if "E11000" in str(e) or "duplicate" in str(e).lower():
            raise_issue(
                "WB.BOARD_EXISTS",
                status=409,
                message="Section already exists on this board for this owner",
                context={"board_id": request.board_id, "owner_id": agent.agent_id},
            )
        raise


@router.patch("/sections/{board_id}/{section_id}")
async def update_section(
    board_id: str,
    section_id: str,
    request: UpdateSectionRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """
    Update a whiteboard section with optimistic locking.
    Requires version field. Returns 409 on version conflict.
    Only the section owner (or an admin) may update. Triggers compact re-render.
    """
    repo = _get_repo()

    # Enforce owner-or-admin BEFORE applying changes (also yields 404 if missing).
    _require_owner_or_admin(repo, board_id, section_id, agent)

    # Build kwargs from non-None fields (excluding version)
    fields: dict[str, Any] = {}
    if request.now is not None:
        fields["now"] = request.now
    if request.next is not None:
        fields["next"] = request.next
    if request.done_recent is not None:
        fields["done_recent"] = request.done_recent
    if request.status is not None:
        fields["status"] = request.status
    if request.note is not None:
        fields["note"] = request.note
    if request.priority is not None:
        fields["priority"] = request.priority

    try:
        updated = repo.update_section(
            board_id=board_id,
            section_id=section_id,
            version=request.version,
            **fields,
        )
    except VersionConflictError as e:
        raise_issue(
            "WB.VERSION_CONFLICT",
            status=409,
            message=f"Version conflict: section is at version {e.current_version}, you sent version {request.version}",
            context={
                "board_id": board_id,
                "section_id": section_id,
                "your_version": request.version,
                "current_version": e.current_version,
            },
        )
    except ValueError as e:
        raise_issue(
            "WB.SECTION_NOT_FOUND",
            status=404,
            message=str(e),
            context={"board_id": board_id, "section_id": section_id},
        )

    # Trigger compact re-render
    _re_render_compact(repo, board_id)

    return ok_response(
        data=updated,
        actions=["get_board", "update_section", "create_section"],
        suggestions=[
            f"View board: GET /api/v1/whiteboard/boards/{board_id}",
        ],
    )


@router.delete("/sections/{board_id}/{section_id}")
async def delete_section(
    board_id: str,
    section_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """Delete a whiteboard section (owner or admin only). Triggers compact re-render."""
    repo = _get_repo()

    # Enforce owner-or-admin; raises WB.SECTION_NOT_FOUND (404) if missing.
    _require_owner_or_admin(repo, board_id, section_id, agent)

    deleted = repo.delete_section(board_id, section_id)
    if not deleted:
        raise_issue(
            "WB.SECTION_NOT_FOUND",
            status=404,
            message=f"Section '{section_id}' not found on board '{board_id}'",
            context={"board_id": board_id, "section_id": section_id},
        )

    # Trigger compact re-render
    _re_render_compact(repo, board_id)

    return ok_response(
        data={"deleted": True, "section_id": section_id},
        actions=["get_board", "create_section"],
    )


# =============================================================================
# SNAPSHOT ENDPOINTS
# =============================================================================

@router.post("/boards/{board_id}/snapshot", status_code=201)
async def create_snapshot(board_id: str, request: CreateSnapshotRequest):
    """Create a snapshot capturing the current board state."""
    repo = _get_repo()
    snapshot = repo.create_snapshot(
        board_id=board_id,
        snapshot_type=SnapshotType(request.snapshot_type),
        trigger=SnapshotTrigger(request.trigger),
    )
    return ok_response(
        data=snapshot,
        http_status=201,
        actions=["get_board", "get_boards"],
    )


@router.get("/boards/{board_id}/snapshots")
async def list_snapshots(board_id: str, limit: int = Query(20, ge=1, le=100)):
    """List snapshots for a board, newest first."""
    repo = _get_repo()
    snapshots = repo.list_snapshots(board_id, limit=limit)
    return ok_response(
        data={"snapshots": snapshots, "total": len(snapshots), "board_id": board_id},
        actions=["get_board", "get_boards"],
    )


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    """Get a single snapshot by ID."""
    repo = _get_repo()
    snapshot = repo.get_snapshot(snapshot_id)
    if not snapshot:
        raise_issue(
            "WB.BOARD_NOT_FOUND",
            status=404,
            message=f"Snapshot '{snapshot_id}' not found",
            context={"snapshot_id": snapshot_id},
        )
    return ok_response(
        data=snapshot,
        actions=["get_board", "get_boards"],
    )
