"""
DAKB Notification Bus - Server-Sent Events Push System

In-memory pub/sub notification system for real-time SSE push notifications.
Integrates with knowledge and messaging write operations to push
updates to connected clients via SSE.

This bus is purely in-memory and has no external dependencies (no Redis),
so it works standalone and degrades cleanly when the broader real-time
stack is disabled.

Version: 1.0

Features:
- In-memory pub/sub for session notifications
- Event buffering for Last-Event-ID resumption
- Auto-cleanup of disconnected sessions
- Integration hooks for write operations

Event Types:
- knowledge/created: New knowledge entry
- knowledge/updated: Knowledge entry updated
- knowledge/voted: Vote cast on knowledge
- message/received: New message in inbox
- message/broadcast: Broadcast message
- session/handoff: Session handoff request
- session/terminated: Session ended

Usage:
    # Get the global notification bus
    bus = await get_notification_bus()

    # Subscribe a session
    async for event in bus.subscribe(session_id, agent_id):
        yield event.to_sse()

    # Publish an event (called from write handlers)
    await bus.publish(
        target_agent_id="recipient",
        event_type="message/received",
        data={"message_id": "msg_xxx", "subject": "Hello"},
    )
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# EVENT MODELS
# =============================================================================

@dataclass
class NotificationEvent:
    """A notification event for SSE delivery."""
    event_id: str
    event_type: str
    data: dict[str, Any]
    target_agent_id: str | None  # None = broadcast to all
    target_session_id: str | None  # None = all sessions for agent
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    retry_ms: int = 5000  # SSE retry hint

    def to_sse(self) -> str:
        """Format as Server-Sent Event."""
        lines = []
        lines.append(f"id: {self.event_id}")
        lines.append(f"event: {self.event_type}")
        lines.append(f"retry: {self.retry_ms}")
        # Data must be a single line or multiple "data:" lines
        data_str = json.dumps(self.data)
        lines.append(f"data: {data_str}")
        lines.append("")  # Empty line terminates event
        return "\n".join(lines) + "\n"


@dataclass
class Subscriber:
    """A subscribed session awaiting events."""
    session_id: str
    agent_id: str
    queue: "asyncio.Queue[NotificationEvent]"
    connected_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_event_id: str | None = None


# =============================================================================
# NOTIFICATION BUS
# =============================================================================

class NotificationBus:
    """
    Pub/sub notification bus for SSE push notifications.

    Thread-safe async implementation supporting:
    - Per-agent/session subscriptions
    - Event buffering for resumption
    - Broadcast events
    - Auto-cleanup of stale subscribers
    """

    # Maximum events to buffer for resumption
    MAX_BUFFER_SIZE = 1000

    # Subscriber timeout (no heartbeat) before cleanup
    SUBSCRIBER_TIMEOUT_SECONDS = 300  # 5 minutes

    def __init__(self, max_buffer_size: int = MAX_BUFFER_SIZE):
        self._max_buffer_size = max_buffer_size
        # Map: agent_id -> list of Subscriber
        self._subscribers: dict[str, list[Subscriber]] = {}
        # Circular buffer for event history (for Last-Event-ID resumption)
        self._event_buffer: deque[NotificationEvent] = deque(
            maxlen=max_buffer_size
        )
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
        # Running flag for background tasks
        self._running = False
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the notification bus background tasks."""
        if self._running:
            return
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Notification bus started")

    async def stop(self) -> None:
        """Stop the notification bus and cleanup."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        # Notify all subscribers of shutdown
        async with self._lock:
            for agent_id, subscribers in self._subscribers.items():
                for sub in subscribers:
                    shutdown_event = NotificationEvent(
                        event_id=(
                            f"evt_{int(time.time() * 1000)}_"
                            f"{uuid.uuid4().hex[:8]}"
                        ),
                        event_type="system/shutdown",
                        data={"message": "Server shutting down"},
                        target_agent_id=agent_id,
                        target_session_id=sub.session_id,
                    )
                    try:
                        sub.queue.put_nowait(shutdown_event)
                    except asyncio.QueueFull:
                        pass
            self._subscribers.clear()
        logger.info("Notification bus stopped")

    async def subscribe(
        self,
        session_id: str,
        agent_id: str,
        last_event_id: str | None = None,
    ) -> AsyncIterator[NotificationEvent]:
        """
        Subscribe a session to receive notifications.

        Args:
            session_id: Session ID
            agent_id: Agent ID
            last_event_id: Last received event ID (for resumption)

        Yields:
            NotificationEvent objects as they arrive
        """
        subscriber = Subscriber(
            session_id=session_id,
            agent_id=agent_id,
            queue=asyncio.Queue(maxsize=100),
            last_event_id=last_event_id,
        )

        # Add subscriber
        async with self._lock:
            if agent_id not in self._subscribers:
                self._subscribers[agent_id] = []
            self._subscribers[agent_id].append(subscriber)
            logger.info(
                "Subscriber added: session=%s, agent=%s", session_id, agent_id
            )

        # Send missed events if last_event_id provided
        if last_event_id:
            await self._send_missed_events(subscriber, last_event_id)

        # Send initial connected event
        connected_event = NotificationEvent(
            event_id=f"evt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            event_type="system/connected",
            data={
                "session_id": session_id,
                "agent_id": agent_id,
                "connected_at": subscriber.connected_at.isoformat(),
            },
            target_agent_id=agent_id,
            target_session_id=session_id,
        )
        await subscriber.queue.put(connected_event)

        try:
            while self._running:
                try:
                    # Wait for event with timeout for heartbeat
                    event = await asyncio.wait_for(
                        subscriber.queue.get(),
                        timeout=30.0,  # 30 second heartbeat interval
                    )
                    yield event
                except asyncio.TimeoutError:
                    # Send heartbeat (UUID suffix prevents ID collisions)
                    heartbeat = NotificationEvent(
                        event_id=(
                            f"heartbeat_{int(time.time() * 1000)}_"
                            f"{uuid.uuid4().hex[:8]}"
                        ),
                        event_type="heartbeat",
                        data={
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        },
                        target_agent_id=agent_id,
                        target_session_id=session_id,
                    )
                    yield heartbeat

        finally:
            # Remove subscriber on disconnect
            async with self._lock:
                if agent_id in self._subscribers:
                    self._subscribers[agent_id] = [
                        s for s in self._subscribers[agent_id]
                        if s.session_id != session_id
                    ]
                    if not self._subscribers[agent_id]:
                        del self._subscribers[agent_id]
            logger.info(
                "Subscriber removed: session=%s, agent=%s",
                session_id, agent_id,
            )

    async def publish(
        self,
        event_type: str,
        data: dict[str, Any],
        target_agent_id: str | None = None,
        target_session_id: str | None = None,
    ) -> str:
        """
        Publish an event to subscribers.

        Args:
            event_type: Event type (e.g., "message/received")
            data: Event data
            target_agent_id: Target agent (None = broadcast)
            target_session_id: Target session (None = all sessions)

        Returns:
            Event ID
        """
        event = NotificationEvent(
            event_id=f"evt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            event_type=event_type,
            data=data,
            target_agent_id=target_agent_id,
            target_session_id=target_session_id,
        )

        # Add to buffer for resumption
        self._event_buffer.append(event)

        # Copy subscriber list under lock, deliver outside lock to avoid
        # lock contention during event delivery.
        subscribers_to_notify: list[Subscriber] = []
        async with self._lock:
            if target_agent_id is None:
                # Broadcast to all - copy all matching subscribers
                for _agent_id, subscribers in self._subscribers.items():
                    for sub in subscribers:
                        if (
                            target_session_id is None
                            or sub.session_id == target_session_id
                        ):
                            subscribers_to_notify.append(sub)
            else:
                # Send to specific agent - copy matching subscribers
                if target_agent_id in self._subscribers:
                    for sub in self._subscribers[target_agent_id]:
                        if (
                            target_session_id is None
                            or sub.session_id == target_session_id
                        ):
                            subscribers_to_notify.append(sub)

        # Deliver events OUTSIDE the lock to prevent contention
        for sub in subscribers_to_notify:
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Queue full for session %s, dropping event", sub.session_id
                )

        logger.debug(
            "Published event: %s -> %s",
            event_type, target_agent_id or "broadcast",
        )
        return event.event_id

    async def _send_missed_events(
        self,
        subscriber: Subscriber,
        last_event_id: str,
    ) -> None:
        """Send events that occurred after last_event_id."""
        found_last = False
        for event in self._event_buffer:
            if found_last:
                # Check if event is for this subscriber
                if self._event_matches_subscriber(event, subscriber):
                    try:
                        await subscriber.queue.put(event)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Queue full during resumption for %s",
                            subscriber.session_id,
                        )
                        break
            elif event.event_id == last_event_id:
                found_last = True

        if not found_last:
            # Last event not in buffer - send warning
            logger.warning(
                "Last event %s not found in buffer for session %s",
                last_event_id, subscriber.session_id,
            )

    def _event_matches_subscriber(
        self, event: NotificationEvent, subscriber: Subscriber
    ) -> bool:
        """Check if event should be delivered to subscriber."""
        # Broadcast events go to all
        if event.target_agent_id is None:
            return True

        # Check agent match
        if event.target_agent_id != subscriber.agent_id:
            return False

        # Check session match (if specified)
        if event.target_session_id is not None:
            return event.target_session_id == subscriber.session_id

        return True

    async def _cleanup_loop(self) -> None:
        """Background task to cleanup stale subscribers and prune old events."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Run every minute

                # 1. Cleanup stale subscribers (no recent activity)
                stale_session_ids: list[str] = []
                now = datetime.now(timezone.utc)

                async with self._lock:
                    for agent_id, subscribers in list(
                        self._subscribers.items()
                    ):
                        for sub in subscribers:
                            # Connected too long without activity?
                            elapsed = (now - sub.connected_at).total_seconds()
                            if elapsed > self.SUBSCRIBER_TIMEOUT_SECONDS:
                                stale_session_ids.append(sub.session_id)

                        # Remove stale subscribers from this agent's list
                        if stale_session_ids:
                            self._subscribers[agent_id] = [
                                s for s in subscribers
                                if s.session_id not in stale_session_ids
                            ]
                            if not self._subscribers[agent_id]:
                                del self._subscribers[agent_id]

                if stale_session_ids:
                    logger.info(
                        "Cleaned up %d stale notification subscribers",
                        len(stale_session_ids),
                    )

                # 2. Prune old events from buffer (keep only recent ones).
                # Events older than 5 minutes are unlikely to be resumed.
                prune_before = now.timestamp() - 300  # 5 minutes
                events_before = len(self._event_buffer)

                # Filter events (deque has no direct filter, so rebuild)
                recent_events = [
                    evt for evt in self._event_buffer
                    if evt.timestamp.timestamp() > prune_before
                ]

                if len(recent_events) < events_before:
                    self._event_buffer.clear()
                    self._event_buffer.extend(recent_events)
                    pruned_count = events_before - len(recent_events)
                    if pruned_count > 0:
                        logger.debug(
                            "Pruned %d old events from buffer", pruned_count
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Notification bus cleanup error: %s", e)

    def get_subscriber_count(self, agent_id: str | None = None) -> int:
        """Get number of subscribers (optionally for specific agent)."""
        if agent_id:
            return len(self._subscribers.get(agent_id, []))
        return sum(len(subs) for subs in self._subscribers.values())


# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

_notification_bus: NotificationBus | None = None
_bus_lock = asyncio.Lock()


async def get_notification_bus() -> NotificationBus:
    """Get or create the global notification bus instance."""
    global _notification_bus
    async with _bus_lock:
        if _notification_bus is None:
            _notification_bus = NotificationBus()
            await _notification_bus.start()
        return _notification_bus


async def shutdown_notification_bus() -> None:
    """Shutdown the global notification bus."""
    global _notification_bus
    async with _bus_lock:
        if _notification_bus is not None:
            await _notification_bus.stop()
            _notification_bus = None


# =============================================================================
# INTEGRATION HELPERS
# =============================================================================

async def notify_message_received(
    recipient_agent_id: str,
    message_id: str,
    sender_id: str,
    subject: str,
    priority: str,
) -> None:
    """
    Notify agent of new message.

    Call from messaging route after successful message creation.
    """
    bus = await get_notification_bus()
    await bus.publish(
        event_type="message/received",
        data={
            "message_id": message_id,
            "sender_id": sender_id,
            "subject": subject,
            "priority": priority,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        target_agent_id=recipient_agent_id,
    )


async def notify_message_broadcast(
    sender_id: str,
    message_id: str,
    subject: str,
    priority: str,
) -> None:
    """
    Notify all agents of broadcast message.

    Call from messaging route after successful broadcast creation.
    """
    bus = await get_notification_bus()
    await bus.publish(
        event_type="message/broadcast",
        data={
            "message_id": message_id,
            "sender_id": sender_id,
            "subject": subject,
            "priority": priority,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        target_agent_id=None,  # Broadcast to all
    )


async def notify_knowledge_created(
    creator_agent_id: str,
    knowledge_id: str,
    title: str,
    category: str,
) -> None:
    """
    Notify about new knowledge creation.

    Broadcasts to all agents for awareness.
    """
    bus = await get_notification_bus()
    await bus.publish(
        event_type="knowledge/created",
        data={
            "knowledge_id": knowledge_id,
            "title": title,
            "category": category,
            "creator": creator_agent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        target_agent_id=None,  # Broadcast to all
    )


async def notify_session_handoff(
    source_session_id: str,
    target_agent_id: str,
    handoff_data: dict[str, Any],
) -> None:
    """
    Notify agent of incoming session handoff.

    Call from session route when handoff package is created.
    """
    bus = await get_notification_bus()
    await bus.publish(
        event_type="session/handoff",
        data={
            "source_session_id": source_session_id,
            "handoff_data": handoff_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        target_agent_id=target_agent_id,
    )
