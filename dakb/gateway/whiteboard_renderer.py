"""
HIVE Whiteboard Renderer

Renders whiteboard sections into compact (~1K) and full (~5K) text views
wrapped in <hive-whiteboard> XML tags for agent context injection.

Compact view:
- Budget: board.settings.compact_max_chars (default 1024)
- Per-category budgets: blocked 200, agents 400, goals 100, recent 74
- Priority cascade: blocked/critical always shown, active first, stale/idle dropped
- Sorted by updated_at desc within each category

Full view:
- Budget: board.settings.full_max_chars (default 5120)
- Includes stale and idle sections
- Shows status tags [active], [stale], [BLOCKED]

Version: 2.0
"""

from __future__ import annotations

from datetime import datetime, timezone

# Per-category character budgets for compact render
BUDGET_BLOCKED = 200
BUDGET_AGENTS = 400
BUDGET_GOALS = 100
BUDGET_RECENT = 74

DEFAULT_COMPACT_MAX = 1024
DEFAULT_FULL_MAX = 5120

EMPTY_PLACEHOLDER = "No active agents — all systems idle"

XML_CLOSE = "</hive-whiteboard>"


# =============================================================================
# HELPERS
# =============================================================================


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if over max_len."""
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."


def _format_agent_line(section: dict, max_len: int = 70) -> str:
    """Format a single agent line for compact view."""
    alias = section.get("owner_alias") or "?"
    now = section.get("now") or "Idle"

    if section.get("is_blocked"):
        note = section.get("note") or ""
        line = f"  {alias} [BLOCKED]: {note}"
    else:
        line = f"  {alias}: {now}"

    return _truncate(line, max_len)


def _format_goal_line(text: str, done: bool = False, max_len: int = 50) -> str:
    """Format a single goal line for compact view."""
    prefix = "[x] " if done else "[ ] "
    return _truncate(prefix + text, max_len)


def _xml_open(board: dict) -> str:
    """Build opening XML tag with updated timestamp."""
    updated_at = board.get("updated_at", datetime.now(timezone.utc).isoformat())
    return f'<hive-whiteboard updated="{updated_at}">'


def _get_compact_max(board: dict) -> int:
    """Get compact max chars from board settings."""
    settings = board.get("settings") or {}
    return settings.get("compact_max_chars", DEFAULT_COMPACT_MAX)


def _get_full_max(board: dict) -> int:
    """Get full max chars from board settings."""
    settings = board.get("settings") or {}
    return settings.get("full_max_chars", DEFAULT_FULL_MAX)


def _sort_by_updated(sections: list[dict]) -> list[dict]:
    """Sort sections by updated_at descending."""
    def key(s):
        val = s.get("updated_at", "")
        return val if isinstance(val, str) else ""
    return sorted(sections, key=key, reverse=True)


def _is_blocked(section: dict) -> bool:
    return bool(section.get("is_blocked")) or section.get("status") == "blocked"


def _is_critical(section: dict) -> bool:
    return section.get("priority") == "critical"


def _is_active(section: dict) -> bool:
    return section.get("status") == "active"


def _is_stale(section: dict) -> bool:
    return section.get("status") == "stale"


def _is_idle(section: dict) -> bool:
    return section.get("status") == "idle"


def _section_type(section: dict) -> str:
    return section.get("section_type", "")


# =============================================================================
# FULL VIEW SECTION RENDERERS
# =============================================================================


def _render_agent_full(section: dict) -> str:
    """Render a single agent_status section for full view."""
    alias = section.get("owner_alias") or "?"
    status = (
        section.get("status", "active").upper()
        if _is_blocked(section)
        else section.get("status", "active")
    )
    now = section.get("now") or "Idle"
    next_task = section.get("next") or ""
    note = section.get("note") or ""

    tag = "BLOCKED" if _is_blocked(section) else status
    lines = [f"### {alias} [{tag}]"]
    lines.append(f"  NOW: {now}")
    if next_task:
        lines.append(f"  NEXT: {next_task}")
    if note:
        lines.append(f"  NOTE: {note}")

    done = section.get("done_recent") or []
    if done:
        lines.append(f"  DONE: {', '.join(done[:3])}")

    return "\n".join(lines)


# =============================================================================
# PUBLIC API
# =============================================================================


def render_compact(board: dict, sections: list[dict]) -> str:
    """
    Render whiteboard sections into compact text.

    Budget from board.settings.compact_max_chars (default 1024).
    Per-category budgets: blocked 200, agents 400, goals 100, recent 74.

    Priority cascade:
    1. ALWAYS show is_blocked=True or priority="critical"
    2. Active agents (idle/stale dropped)
    3. Goals (open first)
    4. Recent completions (max 3)

    Output wrapped in <hive-whiteboard updated="TIMESTAMP">...</hive-whiteboard>

    Args:
        board: Board dict with settings
        sections: List of section dicts

    Returns:
        Rendered string wrapped in XML tags
    """
    compact_max = _get_compact_max(board)
    open_tag = _xml_open(board)

    if not sections:
        return f"{open_tag}{EMPTY_PLACEHOLDER}{XML_CLOSE}"

    # Categorize sections
    agents = [s for s in sections if _section_type(s) == "agent_status"]
    goals = [s for s in sections if _section_type(s) == "team_goals"]
    recent = [s for s in sections if _section_type(s) == "recent_completions"]

    # Sort within categories by updated_at desc
    agents = _sort_by_updated(agents)
    goals = _sort_by_updated(goals)
    recent = _sort_by_updated(recent)

    # Separate blocked/critical from active; drop stale/idle
    blocked_agents = [s for s in agents if _is_blocked(s) or _is_critical(s)]
    active_agents = [
        s
        for s in agents
        if _is_active(s) and not _is_critical(s) and not _is_blocked(s)
    ]

    parts = []

    # 1. Blocked/critical (always shown)
    if blocked_agents:
        blocked_lines = []
        budget_remaining = BUDGET_BLOCKED
        for s in blocked_agents:
            line = _format_agent_line(s, max_len=min(70, budget_remaining))
            blocked_lines.append(line)
            budget_remaining -= len(line) + 1
            if budget_remaining <= 0:
                break
        blocked_text = "## Blocked\n" + "\n".join(blocked_lines)
        parts.append(_truncate(blocked_text, BUDGET_BLOCKED))

    # 2. Active agents
    if active_agents:
        agent_lines = []
        budget_remaining = BUDGET_AGENTS
        for s in active_agents:
            line = _format_agent_line(s, max_len=min(70, budget_remaining))
            if budget_remaining - len(line) - 1 < 0 and agent_lines:
                break
            agent_lines.append(line)
            budget_remaining -= len(line) + 1
        if agent_lines:
            agent_text = "## Agents\n" + "\n".join(agent_lines)
            parts.append(_truncate(agent_text, BUDGET_AGENTS))

    # 3. Goals (open first)
    if goals:
        goal_parts = []
        for s in goals:
            note = s.get("note") or ""
            if note:
                done = s.get("status") == "idle"  # treat idle goals as done
                goal_parts.append(_format_goal_line(note, done=done, max_len=50))
        if goal_parts:
            goals_text = "Goals: " + "; ".join(goal_parts)
            parts.append(_truncate(goals_text, BUDGET_GOALS))

    # 4. Recent completions (max 3)
    if recent:
        items = []
        for s in recent:
            items.extend(s.get("done_recent") or [])
        items = items[:3]
        if items:
            recent_text = "Recent: " + "; ".join(items)
            parts.append(_truncate(recent_text, BUDGET_RECENT))

    inner = "\n".join(parts)
    inner = _truncate(inner, compact_max)

    return f"{open_tag}{inner}{XML_CLOSE}"


def render_full(board: dict, sections: list[dict]) -> str:
    """
    Render whiteboard sections into full text.

    Budget from board.settings.full_max_chars (default 5120).
    Includes all sections: active, blocked, idle, stale.
    Shows status tags [active], [stale], [BLOCKED].

    Output wrapped in <hive-whiteboard updated="TIMESTAMP">...</hive-whiteboard>

    Args:
        board: Board dict with settings
        sections: List of section dicts

    Returns:
        Rendered string wrapped in XML tags
    """
    full_max = _get_full_max(board)
    open_tag = _xml_open(board)

    if not sections:
        return f"{open_tag}{EMPTY_PLACEHOLDER}{XML_CLOSE}"

    # Categorize
    agents = [s for s in sections if _section_type(s) == "agent_status"]
    goals = [s for s in sections if _section_type(s) == "team_goals"]
    recent = [s for s in sections if _section_type(s) == "recent_completions"]
    other = [
        s
        for s in sections
        if _section_type(s)
        not in ("agent_status", "team_goals", "recent_completions")
    ]

    agents = _sort_by_updated(agents)

    parts = []

    # Project summaries
    for s in other:
        note = s.get("note") or ""
        if note:
            parts.append(f"## {s.get('section_type', 'Other')}\n{note}")

    # Blocked agents first
    blocked = [s for s in agents if _is_blocked(s)]
    if blocked:
        parts.append("## Blocked")
        for s in blocked:
            parts.append(_render_agent_full(s))

    # Active agents (exclude already-shown blocked)
    active = [s for s in agents if _is_active(s) and not _is_blocked(s)]
    if active:
        parts.append("## Agents")
        for s in active:
            parts.append(_render_agent_full(s))

    # Stale agents
    stale = [s for s in agents if _is_stale(s)]
    if stale:
        parts.append("## Stale")
        for s in stale:
            parts.append(_render_agent_full(s))

    # Idle agents
    idle = [s for s in agents if _is_idle(s)]
    if idle:
        parts.append("## Idle")
        for s in idle:
            parts.append(_render_agent_full(s))

    # Goals
    if goals:
        goal_lines = []
        for s in goals:
            note = s.get("note") or ""
            if note:
                goal_lines.append(f"- {note}")
        if goal_lines:
            parts.append("## Goals\n" + "\n".join(goal_lines))

    # Recent completions
    if recent:
        items = []
        for s in recent:
            items.extend(s.get("done_recent") or [])
        if items:
            numbered = [f"{i + 1}. {item}" for i, item in enumerate(items[:10])]
            parts.append("## Recent Completions\n" + "\n".join(numbered))

    inner = "\n".join(parts)
    inner = _truncate(inner, full_max)

    return f"{open_tag}{inner}{XML_CLOSE}"
