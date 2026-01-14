"""
DAKB Admin Dashboard Module

Provides web-based administration for DAKB:
- Dynamic admin agent management
- Runtime configuration
- Token registry management
- Service status monitoring

Version: 1.0
Created: 2026-01-14
Author: Claude Opus 4.5
"""

from .api import router as admin_api_router
from .api import dashboard_router as admin_dashboard_router
from .websocket import router as admin_ws_router

__all__ = [
    "admin_api_router",
    "admin_dashboard_router",
    "admin_ws_router",
]
