"""
HIVE Whiteboard Auto-Lifecycle Triggers

Called by session handlers (MCP + REST) to auto-update whiteboard sections
when agents start/end sessions, complete tasks, or send heartbeats.

Trigger points:
- session_start → create/update agent section with NOW = task_description
- session_end → set status = idle, move NOW → DONE
- task_complete → append to done_recent, update NOW
- heartbeat → refresh expires_at

Version: 1.0
"""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _get_repo():
    """Get the whiteboard repository from the routes module (lazy import to avoid circular)."""
    try:
        from .routes.whiteboard import _repo
        return _repo
    except Exception:
        return None


def _resolve_board_id(project_path: str | None) -> str | None:
    """Resolve project_path to board_id. Returns None if no match."""
    if not project_path:
        return None
    repo = _get_repo()
    if not repo:
        return None
    # Try to find a board with this project_path
    boards = repo.list_boards(board_type="project")
    for board in boards:
        if board.get("project_path") and project_path.rstrip("/") == board["project_path"].rstrip("/"):
            return board["board_id"]
    # Fallback: derive board_id from last path component
    name = project_path.rstrip("/").split("/")[-1].lower().replace("-", "_").replace(" ", "_")
    board = repo.get_board(f"board_{name}")
    return f"board_{name}" if board else None


def _get_section_id(alias: str) -> str:
    """Generate section_id from agent alias."""
    safe = alias.lower().replace(" ", "_").replace("-", "-")
    return f"sec_agent_status_{safe}"


def _re_render(board_id: str):
    """Re-render compact view after changes."""
    try:
        repo = _get_repo()
        if not repo:
            return
        from .whiteboard_renderer import render_compact
        board = repo.get_board(board_id)
        if board:
            sections = repo.get_sections(board_id)
            compact = render_compact(board, sections)
            repo.update_compact_render(board_id, compact)
    except Exception as e:
        logger.warning(f"Whiteboard re-render failed for {board_id}: {e}")


# =============================================================================
# TRIGGER: SESSION START
# =============================================================================

def on_session_start(
    agent_id: str,
    agent_alias: str,
    project_path: str | None,
    task_description: str,
) -> dict | None:
    """
    Called when an agent starts a session.
    Creates or updates the agent's whiteboard section with NOW = task_description.
    """
    repo = _get_repo()
    if not repo:
        logger.debug("Whiteboard repo not available, skipping session_start trigger")
        return None

    board_id = _resolve_board_id(project_path)
    if not board_id:
        logger.debug(f"No whiteboard board found for project_path={project_path}")
        return None

    section_id = _get_section_id(agent_alias)
    stale_timeout = 900  # 15 minutes default

    try:
        # Try to get existing section
        existing = repo.get_section(board_id, section_id)

        if existing:
            # Update existing section — bypass version check for lifecycle events
            result = repo.update_section(
                board_id=board_id,
                section_id=section_id,
                version=existing["version"],
                now=task_description,
                status="active",
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=stale_timeout),
            )
            logger.info(f"Whiteboard: updated {section_id} on {board_id} → active: {task_description}")
        else:
            # Create new section
            result = repo.create_section(
                board_id=board_id,
                section_type="agent_status",
                owner_id=agent_id,
                owner_alias=agent_alias,
                now=task_description,
            )
            logger.info(f"Whiteboard: created {section_id} on {board_id} → {task_description}")

        _re_render(board_id)
        return result

    except Exception as e:
        logger.warning(f"Whiteboard session_start trigger failed: {e}")
        return None


# =============================================================================
# TRIGGER: SESSION END
# =============================================================================

def on_session_end(
    agent_id: str,
    agent_alias: str,
    project_path: str | None,
    summary: str | None = None,
) -> dict | None:
    """
    Called when an agent ends a session.
    Moves NOW → done_recent, sets status = idle.
    """
    repo = _get_repo()
    if not repo:
        return None

    board_id = _resolve_board_id(project_path)
    if not board_id:
        return None

    section_id = _get_section_id(agent_alias)

    try:
        existing = repo.get_section(board_id, section_id)
        if not existing:
            return None

        # Move current NOW to done_recent
        done_recent = existing.get("done_recent", [])
        current_now = existing.get("now")
        if current_now and current_now != "Idle":
            done_recent = [current_now] + done_recent[:4]  # Keep last 5

        result = repo.update_section(
            board_id=board_id,
            section_id=section_id,
            version=existing["version"],
            now=None,
            status="idle",
            done_recent=done_recent,
            expires_at=None,  # No expiry for idle
        )
        logger.info(f"Whiteboard: {section_id} on {board_id} → idle")

        _re_render(board_id)
        return result

    except Exception as e:
        logger.warning(f"Whiteboard session_end trigger failed: {e}")
        return None


# =============================================================================
# TRIGGER: TASK COMPLETE
# =============================================================================

def on_task_complete(
    agent_id: str,
    agent_alias: str,
    project_path: str | None,
    task_name: str,
    next_task: str | None = None,
) -> dict | None:
    """
    Called when an agent completes a task.
    Appends task_name to done_recent, updates NOW to next_task.
    """
    repo = _get_repo()
    if not repo:
        return None

    board_id = _resolve_board_id(project_path)
    if not board_id:
        return None

    section_id = _get_section_id(agent_alias)

    try:
        existing = repo.get_section(board_id, section_id)
        if not existing:
            return None

        done_recent = existing.get("done_recent", [])
        done_recent = [task_name] + done_recent[:4]

        result = repo.update_section(
            board_id=board_id,
            section_id=section_id,
            version=existing["version"],
            now=next_task,
            done_recent=done_recent,
        )
        logger.info(f"Whiteboard: {section_id} completed '{task_name}', now='{next_task}'")

        _re_render(board_id)
        return result

    except Exception as e:
        logger.warning(f"Whiteboard task_complete trigger failed: {e}")
        return None


# =============================================================================
# TRIGGER: HEARTBEAT
# =============================================================================

def on_heartbeat(
    agent_alias: str,
    project_path: str | None,
) -> bool:
    """
    Called on session heartbeat.
    Refreshes the section's expires_at to prevent stale detection.
    """
    repo = _get_repo()
    if not repo:
        return False

    board_id = _resolve_board_id(project_path)
    if not board_id:
        return False

    section_id = _get_section_id(agent_alias)
    stale_timeout = 900

    try:
        existing = repo.get_section(board_id, section_id)
        if not existing:
            return False

        repo.update_section(
            board_id=board_id,
            section_id=section_id,
            version=existing["version"],
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=stale_timeout),
        )
        return True

    except Exception as e:
        logger.debug(f"Whiteboard heartbeat trigger failed: {e}")
        return False
