"""
DAKB - Distributed Agent Knowledge Base
=======================================

A shared knowledge system for multi-agent AI collaboration.

Features:
- Knowledge Management: Store, search, and vote on shared insights
- Cross-Agent Messaging: Send messages between agents on any machine
- Session Management: Track work sessions and enable handoffs
- MCP Integration: Native support for Claude Code via MCP protocol

Quick Start:
    # As MCP server (Claude Code)
    Add to .mcp.json and use dakb_* tools

    # As Python library
    from dakb.packages.dakb_client import DAKBClient
    client = DAKBClient(gateway_url="http://localhost:3100", token="...")
    results = client.search("error handling patterns")

Example:
    >>> from dakb import __version__
    >>> print(__version__)
    '3.0.0'

For more information, see:
- Documentation: https://github.com/yourusername/dakb#documentation
- MCP Tools: https://github.com/yourusername/dakb/blob/main/docs/mcp-integration.md

License: Apache 2.0
"""

__version__ = "3.0.0"
__author__ = "DAKB Contributors"
__license__ = "Apache-2.0"

# Version info tuple for programmatic access
VERSION_INFO = (3, 0, 0)

# Package metadata
__all__ = [
    "__version__",
    "__author__",
    "__license__",
    "VERSION_INFO",
]
