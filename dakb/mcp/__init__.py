"""
DAKB MCP Server
===============

Model Context Protocol server for Claude Code integration.

Provides 36 tools across two profiles:
- Standard (12 tools): Optimized for typical usage
- Full (36 tools): All operations available

Tools:
- Knowledge: store, search, get, vote
- Messaging: send, get_messages, mark_read, broadcast
- Sessions: start, status, end, export
- Advanced: 24 additional operations via proxy

Usage:
    # In .mcp.json
    {
        "mcpServers": {
            "dakb": {
                "command": "python",
                "args": ["-m", "dakb.mcp"],
                "env": {
                    "DAKB_AUTH_TOKEN": "...",
                    "DAKB_PROFILE": "standard"
                }
            }
        }
    }

Environment Variables:
- DAKB_AUTH_TOKEN: Authentication token (required)
- DAKB_GATEWAY_URL: Gateway URL (default: http://localhost:3100)
- DAKB_PROFILE: Tool profile - "standard" or "full" (default: standard)
"""

__all__ = ["server", "run"]
