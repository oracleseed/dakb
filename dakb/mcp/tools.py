"""
DAKB MCP Tool Definitions

MCP (Model Context Protocol) tool definitions for Claude Code integration.
These tools expose DAKB functionality to Claude Code agents through the MCP protocol.

Version: 1.5
Created: 2025-12-08
Updated: 2025-12-11
Author: Backend Agent (Claude Opus 4.5)

Changelog:
    v1.5 (2025-12-11):
        - Added registration management tools for Self-Registration v1.0 (Phase 5)
        - dakb_create_invite: Admin creates invite token
        - dakb_register_with_invite: Agent self-registers using invite token
        - dakb_revoke_agent: Admin revokes agent access
        - dakb_list_invites: Admin lists invite tokens
        - Standard profile: 12 tools (11 standard + 1 proxy)
        - Full profile: 36 tools

    v1.4 (2025-12-11):
        - Added alias management tools for Token Team system (Phase 5)
        - dakb_register_alias: Register an alias for the current token
        - dakb_list_aliases: List aliases for current token
        - dakb_deactivate_alias: Deactivate an alias
        - dakb_resolve_alias: Resolve alias to token_id
        - Updated dakb_send_message description to mention alias support
        - Updated dakb_get_messages description to note shared inbox behavior
        - Moved alias tools to advanced proxy to reduce standard profile size
        - Standard profile: 12 tools (11 standard + 1 proxy)
        - Full profile: 32 tools

    v1.3 (2025-12-09):
        - Added profile-based tool loading system (standard/full)
        - Added DAKB_ADVANCED_TOOL proxy for advanced features
        - Added get_tools_by_profile() helper function
        - Standard profile: 12 tools (~10k tokens, ~46% reduction)
        - Full profile: 28 tools (~18.5k tokens)

Tools (Basic CRUD - Step 2.1):
- dakb_store_knowledge: Store new knowledge in DAKB
- dakb_search: Semantic search across knowledge base
- dakb_get_knowledge: Retrieve knowledge by ID
- dakb_vote: Vote on knowledge quality
- dakb_status: Check DAKB system status

Tools (Knowledge Management - Step 2.2):
- dakb_bulk_store: Store multiple knowledge entries at once
- dakb_list_by_category: List entries by category with pagination
- dakb_list_by_tags: List entries by tags
- dakb_find_related: Find semantically related entries
- dakb_get_stats: Get detailed knowledge base statistics
- dakb_cleanup_expired: Cleanup expired entries (admin only)

Tools (Voting & Reputation - Step 2.3):
- dakb_get_vote_summary: Get detailed vote summary for knowledge
- dakb_get_agent_reputation: Get reputation metrics for an agent
- dakb_get_leaderboard: Get agent leaderboard by metric
- dakb_get_my_contributions: Get caller's contributions summary
- dakb_flag_for_review: Flag knowledge for moderation review
- dakb_moderate: Moderate flagged knowledge (admin only)

Tools (Messaging - Phase 3):
- dakb_send_message: Send a direct message to another agent (supports aliases)
- dakb_get_messages: Get messages (inbox) for the current agent (shared inbox)
- dakb_mark_read: Mark a message as read
- dakb_broadcast: Send a broadcast message to all agents
- dakb_get_message_stats: Get message statistics for the current agent

Tools (Session Management - Phase 4):
- dakb_session_start: Start a new session for the current agent
- dakb_session_status: Get status of current or specified session
- dakb_session_end: End a session (complete or abandon)
- dakb_session_export: Export session with git context for handoff
- dakb_session_import: Import session from handoff package
- dakb_git_context: Capture and store git context for current session

Tools (Registration Management - Phase 5 Self-Registration v1.0):
- dakb_create_invite: Create invite token (admin-only)
- dakb_register_with_invite: Register agent using invite token
- dakb_revoke_agent: Revoke agent access (admin-only)
- dakb_list_invites: List invite tokens (admin-only)

Tools (Alias Management - Phase 5):
- dakb_register_alias: Register an alias for the current token's team inbox
- dakb_list_aliases: List aliases registered to the current token
- dakb_deactivate_alias: Deactivate (soft delete) an alias
- dakb_resolve_alias: Resolve alias to token_id for debugging

Profile System (v1.3):
- dakb_advanced: Proxy tool for accessing advanced operations (standard profile only)
"""

from typing import Any

# =============================================================================
# PROFILE CONSTANTS
# =============================================================================

PROFILE_STANDARD = "standard"
PROFILE_FULL = "full"


# =============================================================================
# TOOL DEFINITIONS (MCP Schema Format)
# =============================================================================

DAKB_TOOLS: list[dict[str, Any]] = [
    # -------------------------------------------------------------------------
    # dakb_store_knowledge - Store new knowledge in DAKB
    # -------------------------------------------------------------------------
    {
        "name": "dakb_store_knowledge",
        "description": (
            "Store new knowledge in the Distributed Agent Knowledge Base (DAKB). "
            "Use this tool to share insights, lessons learned, patterns, error fixes, "
            "research findings, and other valuable information with other agents. "
            "Knowledge is automatically embedded for semantic search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Brief, descriptive title for the knowledge entry "
                        "(max 100 characters)"
                    ),
                    "maxLength": 100,
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The knowledge content. Supports markdown formatting. "
                        "Include relevant details, context, and examples."
                    ),
                    "minLength": 1,
                },
                "content_type": {
                    "type": "string",
                    "description": "Type of knowledge being stored",
                    "enum": [
                        "lesson_learned",
                        "research",
                        "report",
                        "pattern",
                        "config",
                        "error_fix",
                    ],
                },
                "category": {
                    "type": "string",
                    "description": "Category for organizing the knowledge",
                    "enum": [
                        "database",
                        "ml",
                        "trading",
                        "devops",
                        "security",
                        "frontend",
                        "backend",
                        "general",
                    ],
                },
                "tags": {
                    "type": "array",
                    "description": "Searchable tags for the knowledge (max 10)",
                    "items": {"type": "string"},
                    "maxItems": 10,
                    "default": [],
                },
                "access_level": {
                    "type": "string",
                    "description": (
                        "Access control level. 'public' = all agents, "
                        "'restricted' = specified agents/roles, "
                        "'secret' = explicit access only"
                    ),
                    "enum": ["public", "restricted", "secret"],
                    "default": "public",
                },
                "related_files": {
                    "type": "array",
                    "description": (
                        "Related file paths (e.g., 'dakb/views.py:123')"
                    ),
                    "items": {"type": "string"},
                    "default": [],
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence score for this knowledge (0.0-1.0)",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.8,
                },
            },
            "required": ["title", "content", "content_type", "category"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_search - Semantic search across knowledge base
    # -------------------------------------------------------------------------
    {
        "name": "dakb_search",
        "description": (
            "Search the DAKB knowledge base using semantic similarity. "
            "Returns relevant knowledge entries ranked by similarity score. "
            "Use this to find existing knowledge before creating new entries, "
            "or to retrieve relevant context for a task."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language search query. Describe what you're "
                        "looking for in plain language."
                    ),
                    "minLength": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 5,
                },
                "category": {
                    "type": "string",
                    "description": "Filter results by category (optional)",
                    "enum": [
                        "database",
                        "ml",
                        "trading",
                        "devops",
                        "security",
                        "frontend",
                        "backend",
                        "general",
                    ],
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum similarity score threshold (0.0-1.0)",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.3,
                },
            },
            "required": ["query"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_get_knowledge - Retrieve full knowledge entry by ID
    # -------------------------------------------------------------------------
    {
        "name": "dakb_get_knowledge",
        "description": (
            "Retrieve a complete knowledge entry by its ID. "
            "Use this to get full details of a knowledge entry found via search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "knowledge_id": {
                    "type": "string",
                    "description": (
                        "The unique identifier of the knowledge entry "
                        "(e.g., 'kn_20251207_abc123')"
                    ),
                },
            },
            "required": ["knowledge_id"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_vote - Vote on knowledge quality
    # -------------------------------------------------------------------------
    {
        "name": "dakb_vote",
        "description": (
            "Vote on the quality of a knowledge entry. "
            "Use this to indicate whether knowledge was helpful, unhelpful, "
            "outdated, or incorrect. Voting helps surface quality content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "knowledge_id": {
                    "type": "string",
                    "description": "The ID of the knowledge entry to vote on",
                },
                "vote": {
                    "type": "string",
                    "description": "Type of vote to cast",
                    "enum": ["helpful", "unhelpful", "outdated", "incorrect"],
                },
                "comment": {
                    "type": "string",
                    "description": (
                        "Optional comment explaining the vote (max 500 chars)"
                    ),
                    "maxLength": 500,
                },
                "used_successfully": {
                    "type": "boolean",
                    "description": (
                        "Whether you successfully used this knowledge to "
                        "complete a task"
                    ),
                },
            },
            "required": ["knowledge_id", "vote"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_status - Check DAKB system status
    # -------------------------------------------------------------------------
    {
        "name": "dakb_status",
        "description": (
            "Check the status of the DAKB system including gateway, "
            "embedding service, and MongoDB connectivity. "
            "Also returns basic statistics about the knowledge base."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # =========================================================================
    # KNOWLEDGE MANAGEMENT TOOLS (Step 2.2)
    # =========================================================================
    # -------------------------------------------------------------------------
    # dakb_bulk_store - Store multiple knowledge entries at once
    # -------------------------------------------------------------------------
    {
        "name": "dakb_bulk_store",
        "description": (
            "Store multiple knowledge entries in a single call. "
            "Use this for batch operations when you have several related "
            "pieces of knowledge to store. Handles partial failures gracefully."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "description": (
                        "Array of knowledge entries to store. Each entry must have "
                        "title, content, content_type, and category."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Brief, descriptive title (max 100 chars)",
                                "maxLength": 100,
                            },
                            "content": {
                                "type": "string",
                                "description": "The knowledge content",
                                "minLength": 1,
                            },
                            "content_type": {
                                "type": "string",
                                "description": "Type of knowledge",
                                "enum": [
                                    "lesson_learned",
                                    "research",
                                    "report",
                                    "pattern",
                                    "config",
                                    "error_fix",
                                ],
                            },
                            "category": {
                                "type": "string",
                                "description": "Knowledge category",
                                "enum": [
                                    "database",
                                    "ml",
                                    "trading",
                                    "devops",
                                    "security",
                                    "frontend",
                                    "backend",
                                    "general",
                                ],
                            },
                            "tags": {
                                "type": "array",
                                "description": "Searchable tags (max 10)",
                                "items": {"type": "string"},
                                "maxItems": 10,
                                "default": [],
                            },
                            "access_level": {
                                "type": "string",
                                "description": "Access control level",
                                "enum": ["public", "restricted", "secret"],
                                "default": "public",
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Confidence score (0.0-1.0)",
                                "minimum": 0.0,
                                "maximum": 1.0,
                                "default": 0.8,
                            },
                        },
                        "required": ["title", "content", "content_type", "category"],
                    },
                    "minItems": 1,
                    "maxItems": 50,
                },
            },
            "required": ["entries"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_list_by_category - List knowledge entries by category
    # -------------------------------------------------------------------------
    {
        "name": "dakb_list_by_category",
        "description": (
            "List knowledge entries by category with pagination. "
            "Use this to browse knowledge in a specific category."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category to filter by",
                    "enum": [
                        "database",
                        "ml",
                        "trading",
                        "devops",
                        "security",
                        "frontend",
                        "backend",
                        "general",
                    ],
                },
                "page": {
                    "type": "integer",
                    "description": "Page number (1-indexed)",
                    "minimum": 1,
                    "default": 1,
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of items per page",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
            },
            "required": ["category"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_list_by_tags - List knowledge entries by tags
    # -------------------------------------------------------------------------
    {
        "name": "dakb_list_by_tags",
        "description": (
            "List knowledge entries that match specified tags. "
            "Can require all tags to match or any tag to match."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "description": "Tags to search for",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 10,
                },
                "match_all": {
                    "type": "boolean",
                    "description": (
                        "If true, entries must match ALL tags. "
                        "If false, entries matching ANY tag are returned."
                    ),
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 50,
                },
            },
            "required": ["tags"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_find_related - Find semantically related knowledge entries
    # -------------------------------------------------------------------------
    {
        "name": "dakb_find_related",
        "description": (
            "Find knowledge entries that are semantically related to a given entry. "
            "Useful for discovering related information or cross-referencing knowledge."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "knowledge_id": {
                    "type": "string",
                    "description": "The ID of the knowledge entry to find related entries for",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of related entries to return",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
            },
            "required": ["knowledge_id"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_get_stats - Get detailed knowledge base statistics
    # -------------------------------------------------------------------------
    {
        "name": "dakb_get_stats",
        "description": (
            "Get detailed statistics about the knowledge base including "
            "total entries, breakdown by category and content type, "
            "access level distribution, and most used tags."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_cleanup_expired - Cleanup expired knowledge entries
    # -------------------------------------------------------------------------
    {
        "name": "dakb_cleanup_expired",
        "description": (
            "Cleanup expired knowledge entries. Admin-only operation. "
            "Use dry_run=true to preview what would be deleted without making changes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "If true, returns list of expired entries without deleting. "
                        "If false, actually deletes expired entries."
                    ),
                    "default": True,
                },
            },
            "required": [],
        },
    },
    # =========================================================================
    # VOTING & REPUTATION TOOLS (Step 2.3)
    # =========================================================================
    # -------------------------------------------------------------------------
    # dakb_get_vote_summary - Get detailed vote summary for knowledge
    # -------------------------------------------------------------------------
    {
        "name": "dakb_get_vote_summary",
        "description": (
            "Get detailed vote summary for a knowledge entry including vote counts, "
            "quality score, and vote history. Use this to understand how a knowledge "
            "entry has been received by other agents."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "knowledge_id": {
                    "type": "string",
                    "description": "The ID of the knowledge entry to get vote summary for",
                },
            },
            "required": ["knowledge_id"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_get_agent_reputation - Get reputation metrics for an agent
    # -------------------------------------------------------------------------
    {
        "name": "dakb_get_agent_reputation",
        "description": (
            "Get reputation metrics for an agent including reputation score, rank, "
            "knowledge contributions, votes cast, and helpful votes received. "
            "If no agent_id is provided, returns the caller's own reputation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": (
                        "Agent identifier to get reputation for. "
                        "If not provided, returns the caller's own reputation."
                    ),
                },
            },
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_get_leaderboard - Get agent leaderboard by metric
    # -------------------------------------------------------------------------
    {
        "name": "dakb_get_leaderboard",
        "description": (
            "Get the agent leaderboard ranked by a specific metric. "
            "Shows top contributors to the knowledge base."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "description": "Metric to rank agents by",
                    "enum": ["reputation", "contributions", "helpfulness"],
                    "default": "reputation",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_get_my_contributions - Get caller's contributions summary
    # -------------------------------------------------------------------------
    {
        "name": "dakb_get_my_contributions",
        "description": (
            "Get a summary of your own contributions to DAKB including "
            "knowledge entries created, votes cast, and reputation history. "
            "Use this for self-assessment and tracking your impact."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_flag_for_review - Flag knowledge for moderation review
    # -------------------------------------------------------------------------
    {
        "name": "dakb_flag_for_review",
        "description": (
            "Flag a knowledge entry for moderation review. "
            "Use this when you find knowledge that is outdated, incorrect, "
            "a duplicate, or spam. Flagged entries will be reviewed by moderators."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "knowledge_id": {
                    "type": "string",
                    "description": "ID of the knowledge entry to flag",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for flagging the knowledge",
                    "enum": ["outdated", "incorrect", "duplicate", "spam"],
                },
                "details": {
                    "type": "string",
                    "description": (
                        "Additional details explaining why this knowledge should be reviewed "
                        "(max 500 characters)"
                    ),
                    "maxLength": 500,
                },
            },
            "required": ["knowledge_id", "reason"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_moderate - Moderate flagged knowledge (admin only)
    # -------------------------------------------------------------------------
    {
        "name": "dakb_moderate",
        "description": (
            "Take moderation action on a knowledge entry. Admin-only operation. "
            "Can approve, deprecate, or delete flagged knowledge."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "knowledge_id": {
                    "type": "string",
                    "description": "ID of the knowledge entry to moderate",
                },
                "action": {
                    "type": "string",
                    "description": "Moderation action to take",
                    "enum": ["approve", "deprecate", "delete"],
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Reason for the moderation action "
                        "(required for deprecate/delete, max 500 characters)"
                    ),
                    "maxLength": 500,
                },
            },
            "required": ["knowledge_id", "action"],
        },
    },
    # =========================================================================
    # MESSAGING TOOLS (Phase 3)
    # =========================================================================
    # -------------------------------------------------------------------------
    # dakb_send_message - Send a direct message to another agent
    # -------------------------------------------------------------------------
    {
        "name": "dakb_send_message",
        "description": (
            "Send a direct message to another agent in the DAKB network. "
            "Use this for inter-agent communication, requesting assistance, "
            "sharing updates, or coordinating work. Messages support threading "
            "and reply functionality. "
            "**Alias Support**: The recipient_id can be either a direct token_id "
            "or an agent alias (e.g., 'Coordinator', 'Backend'). Aliases are "
            "automatically resolved to their owning token's inbox."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recipient_id": {
                    "type": "string",
                    "description": (
                        "Target agent ID to send the message to "
                        "(e.g., 'backend', 'mlx', 'quant')"
                    ),
                },
                "subject": {
                    "type": "string",
                    "description": "Message subject line (max 200 characters)",
                    "maxLength": 200,
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Message body content. Supports markdown formatting. "
                        "Include relevant context and details."
                    ),
                    "minLength": 1,
                },
                "priority": {
                    "type": "string",
                    "description": (
                        "Message priority. 'urgent' triggers immediate notifications. "
                        "'high' gets prioritized processing. 'normal' is default. "
                        "'low' is for non-critical messages."
                    ),
                    "enum": ["low", "normal", "high", "urgent"],
                    "default": "normal",
                },
                "thread_id": {
                    "type": "string",
                    "description": (
                        "Optional thread ID to add this message to an existing thread"
                    ),
                },
                "reply_to_id": {
                    "type": "string",
                    "description": (
                        "Optional message ID this is replying to"
                    ),
                },
                "expires_in_hours": {
                    "type": "integer",
                    "description": "Hours until message expires (default 168 = 7 days)",
                    "minimum": 1,
                    "maximum": 8760,
                    "default": 168,
                },
            },
            "required": ["recipient_id", "subject", "content"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_get_messages - Get messages (inbox) for the current agent
    # -------------------------------------------------------------------------
    {
        "name": "dakb_get_messages",
        "description": (
            "Get messages for your inbox. Returns messages addressed to you "
            "plus broadcast messages, sorted by priority (URGENT first) then by date. "
            "Use this to check for communications from other agents. "
            "**Shared Inbox**: All agents using the same token share an inbox. "
            "Messages sent to any of your token's aliases will appear here. "
            "This enables team collaboration where multiple Claude Code instances "
            "can coordinate via a shared message queue."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by message status",
                    "enum": ["pending", "delivered", "read", "expired"],
                },
                "priority": {
                    "type": "string",
                    "description": "Filter by priority",
                    "enum": ["low", "normal", "high", "urgent"],
                },
                "sender_id": {
                    "type": "string",
                    "description": "Filter by sender agent ID",
                },
                "include_broadcasts": {
                    "type": "boolean",
                    "description": "Include broadcast messages (default true)",
                    "default": True,
                },
                "page": {
                    "type": "integer",
                    "description": "Page number for pagination (1-indexed)",
                    "minimum": 1,
                    "default": 1,
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of messages per page",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
            },
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_mark_read - Mark a message as read
    # -------------------------------------------------------------------------
    {
        "name": "dakb_mark_read",
        "description": (
            "Mark one or more messages as read. Use this to acknowledge "
            "messages you have reviewed. Can mark a single message or "
            "multiple messages in batch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "Single message ID to mark as read",
                },
                "message_ids": {
                    "type": "array",
                    "description": "Multiple message IDs to mark as read (batch operation)",
                    "items": {"type": "string"},
                    "maxItems": 100,
                },
            },
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_broadcast - Send a broadcast message to all agents
    # -------------------------------------------------------------------------
    {
        "name": "dakb_broadcast",
        "description": (
            "Send a broadcast message to all registered agents in the DAKB network. "
            "Use this for important announcements, system updates, or coordinating "
            "work across multiple agents. Use sparingly and with appropriate priority."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Broadcast subject line (max 200 characters)",
                    "maxLength": 200,
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Broadcast message content. Keep it concise and relevant "
                        "to all agents."
                    ),
                    "minLength": 1,
                },
                "priority": {
                    "type": "string",
                    "description": (
                        "Broadcast priority. Use 'urgent' only for critical announcements."
                    ),
                    "enum": ["low", "normal", "high", "urgent"],
                    "default": "normal",
                },
                "expires_in_hours": {
                    "type": "integer",
                    "description": "Hours until broadcast expires (default 168 = 7 days)",
                    "minimum": 1,
                    "maximum": 8760,
                    "default": 168,
                },
            },
            "required": ["subject", "content"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_get_message_stats - Get message statistics for the current agent
    # -------------------------------------------------------------------------
    {
        "name": "dakb_get_message_stats",
        "description": (
            "Get message statistics for your agent including total sent, received, "
            "unread count, and breakdown by priority and message type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    # =========================================================================
    # SESSION MANAGEMENT TOOLS (Phase 4)
    # =========================================================================
    # -------------------------------------------------------------------------
    # dakb_session_start - Start a new session for the current agent
    # -------------------------------------------------------------------------
    {
        "name": "dakb_session_start",
        "description": (
            "Start a new session to track your work context. Sessions preserve "
            "working directory, files being edited, task description, and git state. "
            "Sessions support timeout (auto-abandon) and cross-machine handoff."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "working_directory": {
                    "type": "string",
                    "description": (
                        "Working directory path for the session. Usually the project root "
                        "or repository path."
                    ),
                },
                "task_description": {
                    "type": "string",
                    "description": (
                        "Brief description of the task being worked on (max 500 chars)"
                    ),
                    "maxLength": 500,
                },
                "timeout_minutes": {
                    "type": "integer",
                    "description": (
                        "Auto-timeout in minutes. Session is abandoned if inactive "
                        "for this duration. Default 30 minutes."
                    ),
                    "minimum": 1,
                    "maximum": 1440,
                    "default": 30,
                },
                "working_files": {
                    "type": "array",
                    "description": "List of files being actively edited",
                    "items": {"type": "string"},
                    "default": [],
                },
                "loaded_contexts": {
                    "type": "array",
                    "description": "Context files that have been loaded (e.g., CLAUDE.md)",
                    "items": {"type": "string"},
                    "default": [],
                },
                "parent_session_id": {
                    "type": "string",
                    "description": (
                        "Optional parent session ID if continuing from another session "
                        "(e.g., from handoff)"
                    ),
                },
            },
            "required": ["working_directory"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_session_status - Get status of current or specified session
    # -------------------------------------------------------------------------
    {
        "name": "dakb_session_status",
        "description": (
            "Get status of a session including lifecycle state, git context, "
            "working files, and time tracking. If no session_id provided, "
            "returns your most recent active session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": (
                        "Session ID to check. If not provided, returns your "
                        "most recent active session."
                    ),
                },
            },
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_session_end - End a session (complete or abandon)
    # -------------------------------------------------------------------------
    {
        "name": "dakb_session_end",
        "description": (
            "End a session, marking it as completed or abandoned. "
            "Use 'completed' when task is done, 'abandoned' for incomplete work."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to end",
                },
                "status": {
                    "type": "string",
                    "description": "Final status for the session",
                    "enum": ["completed", "abandoned"],
                    "default": "completed",
                },
            },
            "required": ["session_id"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_session_export - Export session with git context for handoff
    # -------------------------------------------------------------------------
    {
        "name": "dakb_session_export",
        "description": (
            "Export a session for handoff to another agent or machine. "
            "Creates a package containing session state, git context, and "
            "a patch bundle of uncommitted changes. "
            "For LOCAL agents: use output_file or auto-saves to .claude/handoffs/ for large packages. "
            "For REMOTE agents: use store_on_server=true, then import with handoff_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to export",
                },
                "store_on_server": {
                    "type": "boolean",
                    "description": (
                        "Store package on DAKB server for remote agent retrieval. "
                        "Returns handoff_id instead of package_json. Remote agents "
                        "can then import using just the handoff_id. Recommended for "
                        "cross-machine handoffs."
                    ),
                    "default": False,
                },
                "output_file": {
                    "type": "string",
                    "description": (
                        "Path to write the package JSON file (for local agents). "
                        "If specified, the package is written to this file instead of "
                        "returned inline. Use session_import with package_file to import."
                    ),
                },
                "target_agent_id": {
                    "type": "string",
                    "description": (
                        "Target agent for the handoff. If not specified, "
                        "any agent can accept."
                    ),
                },
                "target_machine_id": {
                    "type": "string",
                    "description": (
                        "Target machine for the handoff. If not specified, "
                        "any machine can accept."
                    ),
                },
                "include_git_context": {
                    "type": "boolean",
                    "description": "Include git branch, commit, and status",
                    "default": True,
                },
                "include_patch_bundle": {
                    "type": "boolean",
                    "description": "Include compressed diff of uncommitted changes",
                    "default": True,
                },
                "include_stash": {
                    "type": "boolean",
                    "description": "Include git stash in the patch bundle",
                    "default": False,
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the handoff (max 500 chars)",
                    "maxLength": 500,
                },
                "notes": {
                    "type": "string",
                    "description": "Notes for the receiving agent (max 1000 chars)",
                    "maxLength": 1000,
                },
            },
            "required": ["session_id"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_session_import - Import session from handoff package
    # -------------------------------------------------------------------------
    {
        "name": "dakb_session_import",
        "description": (
            "Import a session from a handoff package. Creates a new session "
            "continuing from the exported state. "
            "For REMOTE agents: use handoff_id from export (store_on_server=true). "
            "For LOCAL agents: use package_file or package_json."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "handoff_id": {
                    "type": "string",
                    "description": (
                        "Handoff ID from session_export (when store_on_server=true). "
                        "The package is fetched from DAKB server. RECOMMENDED for "
                        "remote/cross-machine handoffs. Takes precedence over "
                        "package_file and package_json."
                    ),
                },
                "package_file": {
                    "type": "string",
                    "description": (
                        "Path to file containing the handoff package JSON (for local agents). "
                        "Use this for large packages that were auto-saved by "
                        "session_export (check package_file in export response)."
                    ),
                },
                "package_json": {
                    "type": "string",
                    "description": (
                        "JSON string from dakb_session_export containing the "
                        "handoff package. Only use for small packages that fit "
                        "in MCP response without truncation."
                    ),
                },
                "apply_patch": {
                    "type": "boolean",
                    "description": (
                        "Whether to apply the patch bundle to restore "
                        "uncommitted changes"
                    ),
                    "default": True,
                },
                "target_directory": {
                    "type": "string",
                    "description": (
                        "Override working directory. If not provided, uses "
                        "the directory from the original session."
                    ),
                },
            },
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_git_context - Capture and store git context for current session
    # -------------------------------------------------------------------------
    {
        "name": "dakb_git_context",
        "description": (
            "Capture current git repository state and store it with the session. "
            "Captures branch, commit, uncommitted changes, stash list, and "
            "remote tracking status. Useful for preserving context before "
            "breaks or handoffs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to capture git context for",
                },
                "repository_path": {
                    "type": "string",
                    "description": (
                        "Path to git repository. If not provided, uses the "
                        "session's working directory."
                    ),
                },
                "include_diff_summary": {
                    "type": "boolean",
                    "description": "Include human-readable diff summary",
                    "default": True,
                },
                "max_diff_size_kb": {
                    "type": "integer",
                    "description": "Maximum size for diff summary in KB",
                    "minimum": 1,
                    "maximum": 1024,
                    "default": 100,
                },
            },
            "required": ["session_id"],
        },
    },
    # =========================================================================
    # REGISTRATION MANAGEMENT TOOLS (Phase 5 - Self-Registration v1.0)
    # =========================================================================
    # -------------------------------------------------------------------------
    # dakb_create_invite - Admin creates invite token
    # -------------------------------------------------------------------------
    {
        "name": "dakb_create_invite",
        "description": (
            "Create an invite token for a new agent to register with DAKB. "
            "**Admin-only operation.** The invite token should be shared with "
            "the intended agent who will use it to complete registration. "
            "Tokens are single-use and expire after the specified hours."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_agent_id": {
                    "type": "string",
                    "description": (
                        "Optional hint for intended agent ID (e.g., 'gemini-research-v1')"
                    ),
                    "maxLength": 50,
                },
                "target_agent_type": {
                    "type": "string",
                    "description": "Optional expected agent type",
                    "enum": [
                        "claude",
                        "claude_code",
                        "gpt",
                        "openai",
                        "gemini",
                        "grok",
                        "local",
                        "human",
                    ],
                },
                "expires_in_hours": {
                    "type": "integer",
                    "description": "Token validity in hours (default: 48, max: 168)",
                    "minimum": 1,
                    "maximum": 168,
                    "default": 48,
                },
                "note": {
                    "type": "string",
                    "description": "Optional note about why this invite was created",
                    "maxLength": 500,
                },
            },
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_register_with_invite - Agent self-registers using invite token
    # -------------------------------------------------------------------------
    {
        "name": "dakb_register_with_invite",
        "description": (
            "Register a new agent with DAKB using a valid invite token. "
            "This is the self-service registration endpoint. The invite token "
            "is consumed atomically to prevent race conditions. On success, "
            "returns a full authentication token for immediate use. "
            "Optionally register an alias during registration."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": (
                        "Unique agent identifier. Must be lowercase alphanumeric "
                        "with hyphens, 4-50 characters."
                    ),
                    "minLength": 4,
                    "maxLength": 50,
                },
                "agent_type": {
                    "type": "string",
                    "description": "Type of AI agent",
                    "enum": [
                        "claude",
                        "claude_code",
                        "gpt",
                        "openai",
                        "gemini",
                        "grok",
                        "local",
                        "human",
                    ],
                },
                "invite_token": {
                    "type": "string",
                    "description": (
                        "Valid invite token from admin. "
                        "Format: inv_YYYYMMDD_xxxxxxxxxxxx"
                    ),
                },
                "display_name": {
                    "type": "string",
                    "description": "Human-readable display name for the agent",
                    "maxLength": 100,
                },
                "alias": {
                    "type": "string",
                    "description": (
                        "Optional alias to register (e.g., 'Coordinator', 'Reviewer')"
                    ),
                    "maxLength": 50,
                },
                "alias_role": {
                    "type": "string",
                    "description": (
                        "Role for the alias (e.g., 'orchestration', 'code_review')"
                    ),
                    "maxLength": 100,
                },
            },
            "required": ["agent_id", "agent_type", "invite_token"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_revoke_agent - Admin revokes agent access
    # -------------------------------------------------------------------------
    {
        "name": "dakb_revoke_agent",
        "description": (
            "Revoke an agent's access to DAKB. **Admin-only operation.** "
            "Marks the agent as suspended and deactivates all their aliases. "
            "The agent's existing tokens will be invalidated. "
            "Cannot revoke your own access (use another admin). "
            "Agent records are preserved for audit purposes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID to revoke access for",
                },
            },
            "required": ["agent_id"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_list_invites - Admin lists invite tokens
    # -------------------------------------------------------------------------
    {
        "name": "dakb_list_invites",
        "description": (
            "List invite tokens with optional filtering. **Admin-only operation.** "
            "Returns invite tokens created by admins with their status "
            "(active, used, expired, revoked). Token values are partially "
            "masked for security."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by token status",
                    "enum": ["active", "used", "expired", "revoked"],
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of tokens to return",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 20,
                },
            },
            "required": [],
        },
    },
    # =========================================================================
    # ALIAS MANAGEMENT TOOLS (Phase 5)
    # =========================================================================
    # -------------------------------------------------------------------------
    # dakb_register_alias - Register an alias for the current token
    # -------------------------------------------------------------------------
    {
        "name": "dakb_register_alias",
        "description": (
            "Register a new alias for this token's team inbox. "
            "Aliases enable flexible agent addressing - messages sent to an alias "
            "are routed to the owning token's shared inbox. Each token can have "
            "multiple aliases (e.g., 'Coordinator', 'Backend', 'Reviewer'). "
            "Aliases must be globally unique across all tokens."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "alias": {
                    "type": "string",
                    "description": (
                        "Unique alias name to register (e.g., 'Coordinator', 'Backend'). "
                        "Must be 1-50 characters, globally unique."
                    ),
                    "minLength": 1,
                    "maxLength": 50,
                },
                "role": {
                    "type": "string",
                    "description": (
                        "Optional role for the alias (e.g., 'orchestration', 'code_review')"
                    ),
                    "maxLength": 100,
                },
                "metadata": {
                    "type": "object",
                    "description": (
                        "Optional additional metadata (e.g., display_name, description)"
                    ),
                    "default": {},
                },
            },
            "required": ["alias"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_list_aliases - List aliases for current token
    # -------------------------------------------------------------------------
    {
        "name": "dakb_list_aliases",
        "description": (
            "List all aliases registered to your token. "
            "Returns alias names, roles, and status (active/inactive). "
            "Use this to see what aliases can receive messages for your team."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "If true, only return active aliases (default: true)",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_deactivate_alias - Deactivate an alias
    # -------------------------------------------------------------------------
    {
        "name": "dakb_deactivate_alias",
        "description": (
            "Deactivate (soft delete) an alias. "
            "Deactivated aliases no longer route messages but remain in the "
            "database for audit purposes. Only the owning token can deactivate "
            "its aliases."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "alias": {
                    "type": "string",
                    "description": "The alias name to deactivate",
                    "minLength": 1,
                    "maxLength": 50,
                },
            },
            "required": ["alias"],
        },
    },
    # -------------------------------------------------------------------------
    # dakb_resolve_alias - Resolve alias to token_id (for debugging)
    # -------------------------------------------------------------------------
    {
        "name": "dakb_resolve_alias",
        "description": (
            "Resolve an alias to its owning token_id. "
            "Useful for debugging message routing or verifying alias ownership. "
            "Returns the token_id that will receive messages sent to this alias."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "alias": {
                    "type": "string",
                    "description": "The alias name to resolve",
                    "minLength": 1,
                    "maxLength": 50,
                },
            },
            "required": ["alias"],
        },
    },
]


# =============================================================================
# PROFILE-BASED TOOL LOADING (v1.4)
# =============================================================================

# Standard profile tool names (11 tools + 1 proxy = 12 total)
# These are the most commonly used tools for everyday operations
STANDARD_TOOL_NAMES: list[str] = [
    # Core CRUD (5)
    "dakb_store_knowledge",
    "dakb_search",
    "dakb_get_knowledge",
    "dakb_vote",
    "dakb_status",
    # Essential Management (1)
    "dakb_get_stats",
    # Messaging (5) - Core messaging only
    "dakb_send_message",
    "dakb_get_messages",
    "dakb_mark_read",
    "dakb_broadcast",
    "dakb_get_message_stats",
]

# Advanced tool names (24 tools) - accessible via proxy in standard profile
# or directly in full profile
ADVANCED_TOOL_NAMES: list[str] = [
    # Bulk Operations (3)
    "dakb_bulk_store",
    "dakb_list_by_category",
    "dakb_list_by_tags",
    # Discovery (2)
    "dakb_find_related",
    "dakb_cleanup_expired",
    # Reputation (4)
    "dakb_get_vote_summary",
    "dakb_get_agent_reputation",
    "dakb_get_leaderboard",
    "dakb_get_my_contributions",
    # Moderation (2)
    "dakb_flag_for_review",
    "dakb_moderate",
    # Session Management (6)
    "dakb_session_start",
    "dakb_session_status",
    "dakb_session_end",
    "dakb_session_export",
    "dakb_session_import",
    "dakb_git_context",
    # Registration Management (4) - Admin-only invite/registration tools
    "dakb_create_invite",
    "dakb_register_with_invite",
    "dakb_revoke_agent",
    "dakb_list_invites",
    # Alias Management (4) - For team inbox coordination
    "dakb_register_alias",
    "dakb_list_aliases",
    "dakb_deactivate_alias",
    "dakb_resolve_alias",
]

# Proxy tool for accessing advanced features in standard profile
# This single tool provides access to all 24 advanced operations
DAKB_ADVANCED_TOOL: dict[str, Any] = {
    "name": "dakb_advanced",
    "description": (
        "Access 24 advanced DAKB operations via proxy. Use this when you need "
        "features not in the standard tool set.\n\n"
        "**Available Operations:**\n"
        "- Bulk: bulk_store, list_by_category, list_by_tags\n"
        "- Discovery: find_related, cleanup_expired\n"
        "- Reputation: get_vote_summary, get_agent_reputation, get_leaderboard, "
        "get_my_contributions\n"
        "- Moderation: flag_for_review, moderate\n"
        "- Sessions: session_start, session_status, session_end, session_export, "
        "session_import, git_context\n"
        "- Registration: create_invite, register_with_invite, revoke_agent, "
        "list_invites (admin-only)\n"
        "- Aliases: register_alias, list_aliases, deactivate_alias, resolve_alias\n\n"
        "Pass the operation name and a params object matching the original tool's "
        "inputSchema. Use operation='help' to get parameter details for any operation."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "The advanced operation to perform",
                "enum": [
                    "help",
                    "bulk_store",
                    "list_by_category",
                    "list_by_tags",
                    "find_related",
                    "cleanup_expired",
                    "get_vote_summary",
                    "get_agent_reputation",
                    "get_leaderboard",
                    "get_my_contributions",
                    "flag_for_review",
                    "moderate",
                    "session_start",
                    "session_status",
                    "session_end",
                    "session_export",
                    "session_import",
                    "git_context",
                    "create_invite",
                    "register_with_invite",
                    "revoke_agent",
                    "list_invites",
                    "register_alias",
                    "list_aliases",
                    "deactivate_alias",
                    "resolve_alias",
                ],
            },
            "params": {
                "type": "object",
                "description": (
                    "Parameters for the operation. Structure varies by operation - "
                    "use operation='help' with params={'operation': '<name>'} to see "
                    "required params."
                ),
            },
        },
        "required": ["operation"],
    },
}


def get_tools_by_profile(profile: str) -> list[dict[str, Any]]:
    """
    Get tools based on profile setting.

    Profiles:
    - standard: 12 tools (11 standard + 1 proxy) - core messaging only
    - full: 36 tools (all tools, no proxy)

    Args:
        profile: Either 'standard' or 'full'

    Returns:
        List of tool definitions for the specified profile
    """
    if profile == PROFILE_FULL:
        # Full profile: return all 36 tools, no proxy needed
        # Return a copy to prevent accidental modification of the global list
        return list(DAKB_TOOLS)

    # Standard profile: filter to standard tools + add proxy
    standard_tools = [t for t in DAKB_TOOLS if t["name"] in STANDARD_TOOL_NAMES]
    standard_tools.append(DAKB_ADVANCED_TOOL)  # Add proxy tool
    return standard_tools


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_tool_by_name(name: str) -> dict[str, Any] | None:
    """
    Get a tool definition by name.

    Args:
        name: Tool name to look up.

    Returns:
        Tool definition dict or None if not found.
    """
    # Check proxy tool first
    if name == "dakb_advanced":
        return DAKB_ADVANCED_TOOL

    # Check standard DAKB tools
    for tool in DAKB_TOOLS:
        if tool["name"] == name:
            return tool
    return None


def get_all_tool_names() -> list[str]:
    """
    Get list of all tool names.

    Returns:
        List of tool names.
    """
    return [tool["name"] for tool in DAKB_TOOLS]


def validate_tool_args(name: str, args: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Validate arguments against tool schema.

    Basic validation of required fields and types.

    Args:
        name: Tool name.
        args: Arguments to validate.

    Returns:
        Tuple of (is_valid, error_message).
    """
    tool = get_tool_by_name(name)
    if not tool:
        return False, f"Unknown tool: {name}"

    schema = tool.get("inputSchema", {})
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    # Check required fields
    for field in required:
        if field not in args:
            return False, f"Missing required field: {field}"

    # Basic type validation
    for field, value in args.items():
        if field not in properties:
            continue  # Allow extra fields

        prop_schema = properties[field]
        expected_type = prop_schema.get("type")

        if expected_type == "string" and not isinstance(value, str):
            return False, f"Field '{field}' must be a string"
        elif expected_type == "integer" and not isinstance(value, int):
            return False, f"Field '{field}' must be an integer"
        elif expected_type == "number" and not isinstance(value, (int, float)):
            return False, f"Field '{field}' must be a number"
        elif expected_type == "boolean" and not isinstance(value, bool):
            return False, f"Field '{field}' must be a boolean"
        elif expected_type == "array" and not isinstance(value, list):
            return False, f"Field '{field}' must be an array"

        # Enum validation
        if "enum" in prop_schema and value not in prop_schema["enum"]:
            return False, (
                f"Field '{field}' must be one of: {prop_schema['enum']}"
            )

    return True, None
