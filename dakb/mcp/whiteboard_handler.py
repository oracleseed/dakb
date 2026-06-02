"""
HIVE Whiteboard MCP Handler

Handles the dakb_whiteboard tool by proxying to the gateway REST API.
Uses client._request() to call /api/v1/whiteboard/* endpoints.

Version: 3.0
"""

from __future__ import annotations

import logging
import re
from typing import Any

from dakb.mcp.handlers import ToolResponse

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"read", "update", "clear", "snapshot", "history"}


def _unwrap_envelope(result: dict) -> dict:
    """Unwrap agentic envelope if present. Defensive — handles both wrapped and unwrapped."""
    if isinstance(result, dict) and "data" in result and "meta" in result:
        return result["data"] if result["data"] is not None else result
    return result


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r'[^a-z0-9\s_]', '', slug)
    slug = re.sub(r'\s+', '_', slug)
    return slug


def _resolve_board_id(board_param: str | None) -> str:
    if not board_param or board_param in ("auto", "global"):
        return "board_global"
    if board_param.startswith("project:"):
        name = board_param[len("project:"):]
        return f"board_{_slugify(name)}"
    # If it already looks like a board_id, pass through
    if board_param.startswith("board_"):
        return board_param
    return "board_global"


async def handle_whiteboard(client: Any, arguments: dict[str, Any]) -> ToolResponse:
    """Handle dakb_whiteboard tool calls via gateway REST API."""
    action = arguments.get("action", "")

    if action not in VALID_ACTIONS:
        return ToolResponse(
            success=False,
            error=f"Invalid action: '{action}'. Valid: {', '.join(sorted(VALID_ACTIONS))}",
            error_code="INVALID_ACTION",
        )

    board_id = _resolve_board_id(arguments.get("board", "auto"))

    try:
        if action == "read":
            return await _handle_read(client, board_id, arguments)
        elif action == "update":
            return await _handle_update(client, board_id, arguments)
        elif action == "clear":
            return await _handle_clear(client, board_id, arguments)
        elif action == "snapshot":
            return await _handle_snapshot(client, board_id, arguments)
        elif action == "history":
            return await _handle_history(client, board_id, arguments)
    except Exception as e:
        logger.error(f"Whiteboard {action} failed: {e}")
        return ToolResponse(
            success=False,
            error=f"Whiteboard {action} failed: {str(e)}",
            error_code="WHITEBOARD_ERROR",
        )

    return ToolResponse(success=False, error=f"Unhandled action: {action}", error_code="UNHANDLED_ACTION")


async def _handle_read(client: Any, board_id: str, args: dict[str, Any]) -> ToolResponse:
    """Read board — compact (default) or full view."""
    view = args.get("view", "compact")

    if view == "compact":
        result = await client._request("GET", f"/api/v1/whiteboard/boards/{board_id}/compact")
    elif view == "full":
        result = await client._request("GET", f"/api/v1/whiteboard/boards/{board_id}")
    elif view == "render":
        result = await client._request("GET", f"/api/v1/whiteboard/boards/{board_id}/render")
    else:
        result = await client._request("GET", f"/api/v1/whiteboard/boards/{board_id}/compact")

    return ToolResponse(success=True, data=_unwrap_envelope(result))


async def _handle_update(client: Any, board_id: str, args: dict[str, Any]) -> ToolResponse:
    """Update a section. Auto-creates section if it doesn't exist (upsert)."""
    section_id = args.get("section")
    if not section_id:
        return ToolResponse(
            success=False,
            error="'section' is required for update",
            error_code="MISSING_SECTION",
        )

    version = args.get("version")
    if version is None:
        return ToolResponse(
            success=False,
            error="'version' is required for update (optimistic locking)",
            error_code="MISSING_VERSION",
        )

    # Build update payload
    data: dict[str, Any] = {"version": version}
    for field in ("now", "next", "note", "priority", "is_blocked", "done_recent", "status"):
        if field in args and args[field] is not None:
            data[field] = args[field]

    try:
        result = await client._request(
            "PATCH",
            f"/api/v1/whiteboard/sections/{board_id}/{section_id}",
            data=data,
        )
        return ToolResponse(success=True, data=_unwrap_envelope(result))
    except Exception as e:
        error_msg = str(e)
        if "409" in error_msg or "conflict" in error_msg.lower():
            return ToolResponse(
                success=False,
                error=f"Version conflict on {section_id}. Re-read the board and retry with current version.",
                error_code="VERSION_CONFLICT",
            )
        # Auto-create section if it doesn't exist (404 / not found / resource not found)
        if "404" in error_msg or "not found" in error_msg.lower() or "resource" in error_msg.lower():
            logger.info(f"Section '{section_id}' not found, auto-creating on board '{board_id}'")
            return await _auto_create_section(client, board_id, section_id, args)
        raise


async def _auto_create_section(
    client: Any, board_id: str, section_id: str, args: dict[str, Any]
) -> ToolResponse:
    """Auto-create a section when update finds it missing (upsert)."""
    # Derive owner_alias from section_id (e.g. "agent_alpha" -> "agent_alpha")
    owner_alias = section_id
    # Determine section_type from section_id pattern
    if "goal" in section_id.lower():
        section_type = "team_goals"
    elif "completion" in section_id.lower() or "recent" in section_id.lower():
        section_type = "recent_completions"
    elif "project" in section_id.lower() or "summary" in section_id.lower():
        section_type = "project_summary"
    else:
        section_type = "agent_status"

    create_data: dict[str, Any] = {
        "board_id": board_id,
        "section_type": section_type,
        "owner_id": owner_alias,
        "owner_alias": owner_alias,
    }
    for field in ("now", "next", "note", "done_recent"):
        if field in args and args[field] is not None:
            create_data[field] = args[field]

    try:
        result = await client._request(
            "POST",
            "/api/v1/whiteboard/sections",
            data=create_data,
        )
        logger.info(f"Auto-created whiteboard section '{section_id}' on board '{board_id}'")
        return ToolResponse(success=True, data=_unwrap_envelope(result))
    except Exception as create_err:
        logger.error(f"Failed to auto-create section '{section_id}': {create_err}")
        return ToolResponse(
            success=False,
            error=f"Section '{section_id}' not found and auto-create failed: {create_err}",
            error_code="SECTION_CREATE_FAILED",
        )


async def _handle_clear(client: Any, board_id: str, args: dict[str, Any]) -> ToolResponse:
    """Delete a section."""
    section_id = args.get("section")
    if not section_id:
        return ToolResponse(
            success=False,
            error="'section' is required for clear",
            error_code="MISSING_SECTION",
        )

    result = await client._request(
        "DELETE",
        f"/api/v1/whiteboard/sections/{board_id}/{section_id}",
    )
    return ToolResponse(success=True, data=_unwrap_envelope(result))


async def _handle_snapshot(client: Any, board_id: str, args: dict[str, Any]) -> ToolResponse:
    """Take a snapshot of the current board state."""
    data = {
        "snapshot_type": args.get("snapshot_type", "manual"),
        "trigger": args.get("trigger", "user_request"),
    }
    result = await client._request(
        "POST",
        f"/api/v1/whiteboard/boards/{board_id}/snapshot",
        data=data,
    )
    return ToolResponse(success=True, data=_unwrap_envelope(result))


async def _handle_history(client: Any, board_id: str, args: dict[str, Any]) -> ToolResponse:
    """Get snapshot history for a board."""
    params: dict[str, Any] = {}
    limit = args.get("limit", 10)
    if limit:
        params["limit"] = limit

    result = await client._request(
        "GET",
        f"/api/v1/whiteboard/boards/{board_id}/snapshots",
        params=params,
    )
    return ToolResponse(success=True, data=_unwrap_envelope(result))
