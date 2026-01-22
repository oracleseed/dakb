"""
DAKB MCP Session Management

MCP 2025-03-26 Streamable HTTP session management for DAKB Gateway.
Handles session creation, validation, cleanup, and event buffering.

Version: 1.0
Created: 2025-12-17
Author: Backend Agent (Claude Opus 4.5)
Session: sess_20251217_174528_23429cfe

Features:
- Session ID generation with mcp_ prefix + UUID4
- In-memory session store with TTL-based cleanup
- Session ownership validation (agent_id binding)
- Event buffering for Last-Event-ID resumption
- Configurable cleanup intervals and timeouts

Configuration (environment variables):
- DAKB_MCP_SESSION_CLEANUP_INTERVAL: Cleanup check interval in seconds (default: 300)
- DAKB_MCP_SESSION_TIMEOUT: Session idle timeout in seconds (default: 86400 = 24 hours)
- DAKB_MCP_MAX_SESSIONS_PER_AGENT: Max sessions per agent (default: 50)
- DAKB_MCP_MAX_TOTAL_SESSIONS: Max total sessions (default: 1000)
- DAKB_MCP_EVENT_BUFFER_SIZE: Max events to buffer per session (default: 100)
"""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

def _get_env_int(key: str, default: int) -> int:
    """Get integer environment variable with default."""
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


class MCPSessionConfig:
    """MCP Session configuration loaded from environment."""

    cleanup_interval_seconds: int = _get_env_int("DAKB_MCP_SESSION_CLEANUP_INTERVAL", 300)
    session_timeout_seconds: int = _get_env_int("DAKB_MCP_SESSION_TIMEOUT", 86400)  # 24 hours
    max_sessions_per_agent: int = _get_env_int("DAKB_MCP_MAX_SESSIONS_PER_AGENT", 50)  # Increased from 10
    max_total_sessions: int = _get_env_int("DAKB_MCP_MAX_TOTAL_SESSIONS", 1000)
    event_buffer_size: int = _get_env_int("DAKB_MCP_EVENT_BUFFER_SIZE", 100)


# =============================================================================
# SESSION STATE ENUM
# =============================================================================

class SessionState(str, Enum):
    """MCP Session states."""
    ACTIVE = "active"
    IDLE = "idle"
    TERMINATED = "terminated"


# =============================================================================
# SESSION MODEL
# =============================================================================

@dataclass
class MCPSession:
    """
    MCP Session representing a client connection.

    Attributes:
        session_id: Unique session identifier (mcp_ + UUID4)
        agent_id: Owning agent's identifier (from JWT)
        machine_id: Machine identifier (from JWT)
        created_at: Session creation timestamp
        last_activity: Last activity timestamp (for timeout)
        state: Current session state
        event_buffer: Circular buffer for Last-Event-ID resumption
        event_counter: Counter for generating event IDs
        metadata: Optional session metadata
    """
    session_id: str
    agent_id: str
    machine_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    state: SessionState = SessionState.ACTIVE
    event_buffer: list[dict] = field(default_factory=list)
    event_counter: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    protocol_version: str = "2025-03-26"

    def is_expired(self) -> bool:
        """Check if session has exceeded idle timeout."""
        if self.state == SessionState.TERMINATED:
            return True
        now = datetime.now(timezone.utc)
        elapsed = (now - self.last_activity).total_seconds()
        return elapsed > MCPSessionConfig.session_timeout_seconds

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now(timezone.utc)
        if self.state == SessionState.IDLE:
            self.state = SessionState.ACTIVE

    def terminate(self) -> None:
        """Mark session as terminated."""
        self.state = SessionState.TERMINATED

    def add_event(self, event: dict) -> str:
        """
        Add event to buffer with auto-generated ID.

        Returns:
            Generated event ID
        """
        self.event_counter += 1
        event_id = f"evt_{self.session_id[-12:]}_{self.event_counter:06d}"

        event_entry = {
            "id": event_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": event
        }

        # Circular buffer - drop oldest if full
        if len(self.event_buffer) >= MCPSessionConfig.event_buffer_size:
            self.event_buffer.pop(0)

        self.event_buffer.append(event_entry)
        return event_id

    def get_events_since(self, last_event_id: str) -> list[dict]:
        """
        Get events since the given event ID for resumption.

        Args:
            last_event_id: Last event ID received by client

        Returns:
            List of events after the specified ID
        """
        if not last_event_id:
            return self.event_buffer.copy()

        # Find the index of last_event_id
        found_idx = -1
        for idx, event in enumerate(self.event_buffer):
            if event["id"] == last_event_id:
                found_idx = idx
                break

        if found_idx == -1:
            # Event not found - may have been purged, return all
            return self.event_buffer.copy()

        # Return events after the found index
        return self.event_buffer[found_idx + 1:].copy()

    def to_dict(self) -> dict:
        """Convert session to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "machine_id": self.machine_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "state": self.state.value,
            "event_count": len(self.event_buffer),
            "protocol_version": self.protocol_version,
            "metadata": self.metadata,
        }


# =============================================================================
# SESSION ID GENERATION
# =============================================================================

def generate_session_id() -> str:
    """
    Generate MCP session ID with prefix for easy identification.

    Format: mcp_ + 32 hex characters (UUID4 without dashes)
    Example: mcp_a1b2c3d4e5f6789012345678abcdef01

    Benefits:
    - Prefix identifies MCP sessions in logs
    - UUID4 ensures uniqueness without coordination
    - Hex-only characters safe in headers/URLs

    Returns:
        Generated session ID
    """
    return f"mcp_{uuid.uuid4().hex}"


# =============================================================================
# SESSION STORE
# =============================================================================

class MCPSessionStore:
    """
    In-memory session store for MCP HTTP transport.

    Thread-safe session management with:
    - Session creation with agent binding
    - Session validation (existence, expiry, ownership)
    - TTL-based cleanup task
    - Event buffering per session

    Note: This is a single-instance store. For multi-worker/multi-replica
    deployments, implement a Redis-backed store using the same interface.
    """

    def __init__(self):
        self._sessions: dict[str, MCPSession] = {}
        self._agent_sessions: dict[str, set[str]] = {}  # agent_id -> set of session_ids
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the session store and cleanup task."""
        if self._running:
            return

        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            f"MCP Session Store started. Cleanup interval: "
            f"{MCPSessionConfig.cleanup_interval_seconds}s, "
            f"Timeout: {MCPSessionConfig.session_timeout_seconds}s"
        )

    async def stop(self) -> None:
        """Stop the session store and cleanup task."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("MCP Session Store stopped")

    async def _cleanup_loop(self) -> None:
        """Background task to cleanup expired sessions."""
        while self._running:
            try:
                await asyncio.sleep(MCPSessionConfig.cleanup_interval_seconds)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Session cleanup error: {e}")

    async def _cleanup_expired(self) -> int:
        """
        Remove expired sessions.

        Returns:
            Number of sessions removed
        """
        async with self._lock:
            expired_ids = [
                session_id for session_id, session in self._sessions.items()
                if session.is_expired()
            ]

            for session_id in expired_ids:
                session = self._sessions.pop(session_id, None)
                if session:
                    # Remove from agent index
                    if session.agent_id in self._agent_sessions:
                        self._agent_sessions[session.agent_id].discard(session_id)
                        if not self._agent_sessions[session.agent_id]:
                            del self._agent_sessions[session.agent_id]

            if expired_ids:
                logger.info(f"Cleaned up {len(expired_ids)} expired MCP sessions")

            return len(expired_ids)

    async def create(
        self,
        agent_id: str,
        machine_id: str,
        metadata: dict | None = None
    ) -> MCPSession:
        """
        Create a new MCP session.

        Args:
            agent_id: Agent identifier (from JWT)
            machine_id: Machine identifier (from JWT)
            metadata: Optional session metadata

        Returns:
            Created MCPSession

        Raises:
            ValueError: If max sessions exceeded
        """
        async with self._lock:
            # Check global session limit
            if len(self._sessions) >= MCPSessionConfig.max_total_sessions:
                raise ValueError(
                    f"Maximum total sessions ({MCPSessionConfig.max_total_sessions}) exceeded"
                )

            # Check per-agent session limit
            agent_session_count = len(self._agent_sessions.get(agent_id, set()))
            if agent_session_count >= MCPSessionConfig.max_sessions_per_agent:
                raise ValueError(
                    f"Maximum sessions per agent ({MCPSessionConfig.max_sessions_per_agent}) "
                    f"exceeded for agent {agent_id}"
                )

            # Create session
            session_id = generate_session_id()
            session = MCPSession(
                session_id=session_id,
                agent_id=agent_id,
                machine_id=machine_id,
                metadata=metadata or {}
            )

            # Store session
            self._sessions[session_id] = session

            # Update agent index
            if agent_id not in self._agent_sessions:
                self._agent_sessions[agent_id] = set()
            self._agent_sessions[agent_id].add(session_id)

            logger.info(f"Created MCP session {session_id} for agent {agent_id}")
            return session

    async def get(self, session_id: str) -> MCPSession | None:
        """
        Get session by ID.

        Args:
            session_id: Session identifier

        Returns:
            MCPSession if found and not expired, None otherwise
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session and session.is_expired():
                # Lazy cleanup of expired session
                await self._remove_session_unlocked(session_id)
                return None
            return session

    async def validate(self, session_id: str, agent_id: str) -> MCPSession:
        """
        Validate session exists, is not expired, and belongs to agent.

        Args:
            session_id: Session identifier
            agent_id: Agent identifier (from JWT)

        Returns:
            Validated MCPSession

        Raises:
            SessionNotFoundError: If session doesn't exist or expired
            SessionOwnershipError: If session belongs to different agent
        """
        session = await self.get(session_id)

        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found or expired")

        if session.agent_id != agent_id:
            raise SessionOwnershipError(
                f"Session {session_id} belongs to agent {session.agent_id}, "
                f"not {agent_id}"
            )

        # Update last activity
        session.touch()
        return session

    async def terminate(self, session_id: str, agent_id: str) -> bool:
        """
        Terminate a session.

        Args:
            session_id: Session identifier
            agent_id: Agent identifier (for ownership validation)

        Returns:
            True if terminated, False if not found

        Raises:
            SessionOwnershipError: If session belongs to different agent
        """
        async with self._lock:
            session = self._sessions.get(session_id)

            if not session:
                return False

            if session.agent_id != agent_id:
                raise SessionOwnershipError(
                    f"Cannot terminate session {session_id}: "
                    f"belongs to agent {session.agent_id}, not {agent_id}"
                )

            session.terminate()
            await self._remove_session_unlocked(session_id)

            logger.info(f"Terminated MCP session {session_id}")
            return True

    async def _remove_session_unlocked(self, session_id: str) -> None:
        """Remove session from store (must hold lock)."""
        session = self._sessions.pop(session_id, None)
        if session:
            if session.agent_id in self._agent_sessions:
                self._agent_sessions[session.agent_id].discard(session_id)
                if not self._agent_sessions[session.agent_id]:
                    del self._agent_sessions[session.agent_id]

    async def get_agent_sessions(self, agent_id: str) -> list[MCPSession]:
        """
        Get all sessions for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            List of sessions
        """
        async with self._lock:
            session_ids = self._agent_sessions.get(agent_id, set())
            sessions = []
            for session_id in session_ids:
                session = self._sessions.get(session_id)
                if session and not session.is_expired():
                    sessions.append(session)
            return sessions

    async def get_stats(self) -> dict:
        """
        Get session store statistics.

        Returns:
            Statistics dictionary
        """
        async with self._lock:
            active_count = sum(
                1 for s in self._sessions.values()
                if s.state == SessionState.ACTIVE and not s.is_expired()
            )
            idle_count = sum(
                1 for s in self._sessions.values()
                if s.state == SessionState.IDLE and not s.is_expired()
            )

            return {
                "total_sessions": len(self._sessions),
                "active_sessions": active_count,
                "idle_sessions": idle_count,
                "unique_agents": len(self._agent_sessions),
                "max_total_sessions": MCPSessionConfig.max_total_sessions,
                "max_sessions_per_agent": MCPSessionConfig.max_sessions_per_agent,
                "session_timeout_seconds": MCPSessionConfig.session_timeout_seconds,
                "cleanup_interval_seconds": MCPSessionConfig.cleanup_interval_seconds,
            }


# =============================================================================
# SESSION ERRORS
# =============================================================================

class SessionError(Exception):
    """Base exception for session errors."""
    pass


class SessionNotFoundError(SessionError):
    """Session not found or expired."""
    pass


class SessionOwnershipError(SessionError):
    """Session belongs to a different agent."""
    pass


class SessionLimitError(SessionError):
    """Session limit exceeded."""
    pass


# =============================================================================
# GLOBAL SESSION STORE INSTANCE
# =============================================================================

# Global session store instance (singleton pattern)
_session_store: MCPSessionStore | None = None
# Async lock for thread-safe singleton initialization
_store_lock = asyncio.Lock()


def get_session_store() -> MCPSessionStore:
    """
    Get the global session store instance (synchronous, non-thread-safe).

    Note: For thread-safe initialization in async context, use get_session_store_async().

    Returns:
        MCPSessionStore singleton
    """
    global _session_store
    if _session_store is None:
        _session_store = MCPSessionStore()
    return _session_store


async def get_session_store_async() -> MCPSessionStore:
    """
    Get the global session store instance with async-safe initialization.

    Uses double-check locking pattern to prevent race conditions during
    initialization in concurrent async contexts.

    Returns:
        MCPSessionStore singleton
    """
    global _session_store
    if _session_store is None:
        async with _store_lock:
            # Double-check after acquiring lock
            if _session_store is None:
                _session_store = MCPSessionStore()
    return _session_store


async def initialize_session_store() -> MCPSessionStore:
    """
    Initialize and start the global session store.

    Uses async-safe initialization to prevent race conditions.

    Returns:
        Started MCPSessionStore
    """
    store = await get_session_store_async()
    await store.start()
    return store


async def shutdown_session_store() -> None:
    """Shutdown the global session store."""
    global _session_store
    if _session_store:
        await _session_store.stop()
        _session_store = None
