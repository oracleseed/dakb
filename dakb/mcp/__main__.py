"""
DAKB MCP Server Entry Point

Allows running the MCP server as a Python module:
    python -m backend.dakb_service.mcp

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)
"""

from .server import main

if __name__ == "__main__":
    main()
