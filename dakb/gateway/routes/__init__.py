# DAKB Gateway Routes
"""
API route handlers for DAKB Gateway.

This module contains all API endpoints organized by resource type.

Routers:
- knowledge: Knowledge CRUD and semantic search (/api/v1/knowledge/*)
- moderation: Moderation operations (/api/v1/moderation/*)
- messaging: Inter-agent messaging (/api/v1/messages/*)
- sessions: Session management and handoff (/api/v1/sessions/*)
- aliases: Agent alias management (/api/v1/aliases/*)
- mcp: MCP HTTP transport (/mcp) - Phase 1 implementation

Usage:
    from backend.dakb_service.gateway.routes import (
        knowledge_router,
        moderation_router,
        messaging_router,
        sessions_router,
        aliases_router,
        mcp_router,
    )

    # Include in FastAPI app
    app.include_router(knowledge_router)
    app.include_router(moderation_router)
    app.include_router(messaging_router)
    app.include_router(sessions_router)
    app.include_router(aliases_router)
    app.include_router(mcp_router)  # MCP HTTP transport
"""

from .aliases import router as aliases_router
from .knowledge import router as knowledge_router
from .mcp import router as mcp_router
from .messaging import router as messaging_router
from .moderation import router as moderation_router
from .sessions import router as sessions_router

__all__ = [
    "knowledge_router",
    "moderation_router",
    "messaging_router",
    "sessions_router",
    "aliases_router",
    "mcp_router",
]
