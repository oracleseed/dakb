"""
DAKB Admin WebSocket Routes

Real-time status updates for admin dashboard via WebSocket.

Version: 1.0
Created: 2026-01-14
Author: Claude Opus 4.5
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Set
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..db.collections import get_dakb_client
from ..gateway.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin WebSocket"])


# =============================================================================
# CONNECTION MANAGER
# =============================================================================

class AdminConnectionManager:
    """Manages WebSocket connections for admin status updates."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._broadcast_task: asyncio.Task = None
        self._running: bool = False

    async def connect(self, websocket: WebSocket):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"Admin WebSocket connected. Total: {len(self.active_connections)}")

        # Start broadcast task if not running
        if not self._running and len(self.active_connections) == 1:
            self._running = True
            self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        self.active_connections.discard(websocket)
        logger.info(f"Admin WebSocket disconnected. Total: {len(self.active_connections)}")

        # Stop broadcast task if no connections
        if len(self.active_connections) == 0 and self._broadcast_task:
            self._running = False

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected clients."""
        if not self.active_connections:
            return

        data = json.dumps(message)
        disconnected = set()

        for connection in self.active_connections:
            try:
                if connection.client_state == WebSocketState.CONNECTED:
                    await connection.send_text(data)
            except Exception as e:
                logger.warning(f"WebSocket send error: {e}")
                disconnected.add(connection)

        # Clean up disconnected connections
        for conn in disconnected:
            self.active_connections.discard(conn)

    async def _broadcast_loop(self):
        """Background task to broadcast status updates periodically."""
        while self._running and self.active_connections:
            try:
                status = await self._get_status()
                await self.broadcast({
                    "type": "status_update",
                    "data": status,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            except Exception as e:
                logger.error(f"Broadcast loop error: {e}")

            await asyncio.sleep(5)  # Update every 5 seconds

    async def _get_status(self) -> dict:
        """Get current system status."""
        try:
            settings = get_settings()
            client = get_dakb_client()
            db = client[settings.db_name]

            # Get counts
            total_agents = db["dakb_agents"].count_documents({})
            active_agents = db["dakb_agents"].count_documents({"status": "active"})
            total_knowledge = db["dakb_knowledge"].count_documents({})
            pending_messages = db["dakb_messages"].count_documents({"status": "pending"})

            return {
                "services": {
                    "gateway": {"status": "running"},
                    "embedding": {"status": "running"},
                    "mongodb": {"status": "connected"}
                },
                "agents": {
                    "total": total_agents,
                    "active": active_agents
                },
                "knowledge": {
                    "total": total_knowledge
                },
                "messages": {
                    "pending": pending_messages
                }
            }
        except Exception as e:
            logger.error(f"Status fetch error: {e}")
            return {
                "services": {
                    "gateway": {"status": "unknown"},
                    "embedding": {"status": "unknown"},
                    "mongodb": {"status": "error"}
                },
                "error": str(e)
            }


# Global connection manager instance
manager = AdminConnectionManager()


# =============================================================================
# WEBSOCKET ENDPOINTS
# =============================================================================

@router.websocket("/ws/admin/status")
async def admin_status_ws(websocket: WebSocket):
    """
    WebSocket endpoint for real-time admin status updates.

    Broadcasts system status every 5 seconds to all connected clients.
    """
    await manager.connect(websocket)

    try:
        # Send initial status immediately
        status = await manager._get_status()
        await websocket.send_json({
            "type": "status_update",
            "data": status,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Wait for any messages from client (ping/pong, etc.)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)

                # Handle ping
                if data == "ping":
                    await websocket.send_text("pong")

            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    break

    except WebSocketDisconnect:
        logger.info("Admin WebSocket client disconnected")
    except Exception as e:
        logger.error(f"Admin WebSocket error: {e}")
    finally:
        manager.disconnect(websocket)


@router.websocket("/ws/admin/activity")
async def admin_activity_ws(websocket: WebSocket):
    """
    WebSocket endpoint for real-time activity feed.

    Broadcasts recent activity events as they occur.
    """
    await websocket.accept()

    try:
        while True:
            # For now, just send heartbeat - can be enhanced to
            # stream audit log entries in real-time
            await asyncio.sleep(10)
            await websocket.send_json({
                "type": "heartbeat",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

    except WebSocketDisconnect:
        logger.info("Admin activity WebSocket disconnected")
    except Exception as e:
        logger.error(f"Admin activity WebSocket error: {e}")
