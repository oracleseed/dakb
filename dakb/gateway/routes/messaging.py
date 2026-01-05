"""
DAKB Gateway - Messaging Routes

REST API endpoints for inter-agent messaging operations including
send, receive, mark read, delete, and broadcast functionality.

Version: 1.1
Created: 2025-12-08
Updated: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Changelog v1.1:
- ISS-064 Fix: Added rate limiting for send endpoints

Endpoints:
- POST /api/v1/messages - Send a message
- GET /api/v1/messages - Get messages (inbox)
- GET /api/v1/messages/{message_id} - Get specific message
- POST /api/v1/messages/{message_id}/read - Mark message as read
- DELETE /api/v1/messages/{message_id} - Delete message
- POST /api/v1/messages/broadcast - Send broadcast message
- GET /api/v1/messages/stats - Get message statistics
- GET /api/v1/messages/thread/{thread_id} - Get thread messages
"""

import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field


# =============================================================================
# RATE LIMITING (ISS-064 Fix)
# =============================================================================

# Simple in-memory rate limiter
# For production, use Redis-based solution like fastapi-limiter
_rate_limit_store: dict[str, list[float]] = defaultdict(list)

# Rate limit configuration
RATE_LIMIT_MESSAGES_PER_MINUTE = 60
RATE_LIMIT_BROADCASTS_PER_MINUTE = 5
RATE_LIMIT_WINDOW_SECONDS = 60


def check_rate_limit(
    agent_id: str,
    limit: int,
    window: int = RATE_LIMIT_WINDOW_SECONDS,
    key_suffix: str = ""
) -> bool:
    """
    Check if agent has exceeded rate limit.

    ISS-064 Fix: Simple in-memory rate limiter.

    Args:
        agent_id: Agent identifier
        limit: Maximum requests per window
        window: Time window in seconds
        key_suffix: Optional suffix to distinguish different limits

    Returns:
        True if within limit, False if exceeded
    """
    key = f"{agent_id}:{key_suffix}" if key_suffix else agent_id
    now = time.time()
    cutoff = now - window

    # Clean old entries
    _rate_limit_store[key] = [t for t in _rate_limit_store[key] if t > cutoff]

    if len(_rate_limit_store[key]) >= limit:
        return False

    _rate_limit_store[key].append(now)
    return True


def rate_limit_message(agent_id: str) -> None:
    """Enforce rate limit for message sending."""
    if not check_rate_limit(agent_id, RATE_LIMIT_MESSAGES_PER_MINUTE, key_suffix="msg"):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_MESSAGES_PER_MINUTE} messages per minute"
        )


def rate_limit_broadcast(agent_id: str) -> None:
    """Enforce rate limit for broadcast messages."""
    if not check_rate_limit(agent_id, RATE_LIMIT_BROADCASTS_PER_MINUTE, key_suffix="broadcast"):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_BROADCASTS_PER_MINUTE} broadcasts per minute"
        )

from ..middleware.auth import AuthenticatedAgent, get_current_agent
from ...db.collections import get_dakb_client
from ...messaging import (
    MessageRepository,
    MessageQueue,
    NotificationService,
    Message,
    MessageCreate,
    MessageFilter,
    MessageType,
    MessagePriority,
    MessageStatus,
    MessageResponse,
    MessageListResponse,
    BroadcastResponse,
    MessageStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/messages", tags=["Messaging"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class SendMessageRequest(BaseModel):
    """Request model for sending a message."""
    recipient_id: Optional[str] = Field(
        None,
        description="Target agent ID (None for broadcast)"
    )
    message_type: MessageType = Field(
        default=MessageType.DIRECT,
        description="Type of message"
    )
    priority: MessagePriority = Field(
        default=MessagePriority.NORMAL,
        description="Message priority"
    )
    subject: str = Field(..., max_length=200, description="Message subject")
    content: str = Field(..., description="Message body")
    thread_id: Optional[str] = Field(None, description="Thread to add message to")
    reply_to_id: Optional[str] = Field(None, description="Message being replied to")
    expires_in_hours: int = Field(
        default=168,
        ge=1,
        le=8760,
        description="Hours until expiration"
    )
    metadata: dict = Field(default_factory=dict)


class BroadcastRequest(BaseModel):
    """Request model for broadcast messages."""
    subject: str = Field(..., max_length=200, description="Message subject")
    content: str = Field(..., description="Message body")
    priority: MessagePriority = Field(
        default=MessagePriority.NORMAL,
        description="Message priority"
    )
    expires_in_hours: int = Field(default=168, ge=1, le=8760)
    metadata: dict = Field(default_factory=dict)


class MarkReadRequest(BaseModel):
    """Request model for marking messages as read."""
    message_ids: Optional[List[str]] = Field(
        None,
        description="Message IDs to mark read (if batch operation)"
    )


class MessageQueryParams(BaseModel):
    """Query parameters for message filtering."""
    status: Optional[MessageStatus] = None
    priority: Optional[MessagePriority] = None
    sender_id: Optional[str] = None
    thread_id: Optional[str] = None
    include_broadcasts: bool = True
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


# =============================================================================
# DEPENDENCY HELPERS
# =============================================================================

def get_message_repository() -> MessageRepository:
    """
    Get message repository instance with alias resolver and lister wired.

    Phase 3 & 4 Token Alias System:
    - alias_resolver: Resolves aliases to token_ids for message routing
    - alias_lister: Gets all alias names for a token_id for shared inbox queries
    """
    client = get_dakb_client()

    # Create alias repository for alias resolution
    # Pass agents collection to enable direct agent_id routing
    from ...db.collections import AliasRepository
    alias_repo = AliasRepository(
        collection=client.dakb.dakb_agent_aliases,
        agents_collection=client.dakb.dakb_agents
    )

    # Create message repository with alias callbacks wired
    return MessageRepository(
        collection=client.dakb.dakb_messages,
        alias_resolver=alias_repo.resolve_alias,
        alias_lister=alias_repo.get_alias_names_for_token
    )


def get_message_queue() -> MessageQueue:
    """Get message queue instance."""
    client = get_dakb_client()
    return MessageQueue(
        messages_collection=client.dakb.dakb_messages,
        queue_collection=client.dakb.dakb_message_queue,
    )


# =============================================================================
# SEND ENDPOINTS
# =============================================================================

@router.post(
    "",
    response_model=MessageResponse,
    summary="Send a message",
    description="Send a direct message to another agent or start a thread."
)
async def send_message(
    request: SendMessageRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> MessageResponse:
    """
    Send a message to another agent.

    - Direct messages require recipient_id
    - Use message_type=REPLY with reply_to_id for replies
    - Messages expire after expires_in_hours (default 7 days)
    - Rate limited to 60 messages per minute (ISS-064)
    """
    # ISS-064 Fix: Apply rate limiting
    rate_limit_message(agent.agent_id)

    repo = get_message_repository()

    # Validate message type
    if request.message_type == MessageType.DIRECT and not request.recipient_id:
        raise HTTPException(
            status_code=400,
            detail="Direct messages require recipient_id"
        )

    if request.message_type == MessageType.REPLY and not request.reply_to_id:
        raise HTTPException(
            status_code=400,
            detail="Reply messages require reply_to_id"
        )

    # Check if replying to existing message
    if request.reply_to_id:
        parent = repo.get_by_id(request.reply_to_id)
        if not parent:
            raise HTTPException(
                status_code=404,
                detail=f"Reply target message not found: {request.reply_to_id}"
            )

    try:
        # Create message
        message_create = MessageCreate(
            recipient_id=request.recipient_id,
            message_type=request.message_type,
            priority=request.priority,
            subject=request.subject,
            content=request.content,
            thread_id=request.thread_id,
            reply_to_id=request.reply_to_id,
            expires_in_hours=request.expires_in_hours,
            metadata=request.metadata,
        )

        message = repo.send_message(agent.agent_id, message_create)

        # Enqueue for delivery
        queue = get_message_queue()
        queue.enqueue(message)

        logger.info(
            f"Message {message.message_id} sent by {agent.agent_id} "
            f"to {request.recipient_id or 'thread'}"
        )

        # Phase 2.5: Send SSE notification to recipient
        if request.recipient_id:
            try:
                from ..notification_bus import notify_message_received
                import asyncio
                asyncio.create_task(notify_message_received(
                    recipient_agent_id=request.recipient_id,
                    message_id=message.message_id,
                    sender_id=agent.agent_id,
                    subject=request.subject,
                    priority=request.priority.value,
                ))
            except Exception as e:
                # Don't fail the message send if notification fails
                logger.warning(f"Failed to send SSE notification: {e}")

        return MessageResponse(
            success=True,
            message=message
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        raise HTTPException(status_code=500, detail="Failed to send message")


@router.post(
    "/broadcast",
    response_model=BroadcastResponse,
    summary="Send broadcast message",
    description="Send a message to all registered agents."
)
async def send_broadcast(
    request: BroadcastRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> BroadcastResponse:
    """
    Send a broadcast message to all agents.

    - Broadcasts are delivered to all registered agents
    - Use priority=URGENT for critical system announcements
    - Rate limited to 5 broadcasts per minute (ISS-064)
    """
    # ISS-064 Fix: Apply stricter rate limiting for broadcasts
    rate_limit_broadcast(agent.agent_id)

    repo = get_message_repository()

    try:
        message = repo.send_broadcast(
            sender_id=agent.agent_id,
            subject=request.subject,
            content=request.content,
            priority=request.priority,
            expires_in_hours=request.expires_in_hours,
            metadata=request.metadata,
        )

        # Enqueue for broadcast delivery
        queue = get_message_queue()
        queue.enqueue(message)

        # Get recipient count (all registered agents)
        from ...db.collections import get_dakb_repositories
        client = get_dakb_client()
        repos = get_dakb_repositories(client)
        active_agents = repos["agents"].find_active(since_minutes=60 * 24 * 7)  # Active in last week
        recipients_count = len([a for a in active_agents if a.agent_id != agent.agent_id])

        logger.info(
            f"Broadcast {message.message_id} sent by {agent.agent_id} "
            f"to {recipients_count} agents"
        )

        # Phase 2.5: Send SSE notification for broadcast
        try:
            from ..notification_bus import notify_message_broadcast
            import asyncio
            asyncio.create_task(notify_message_broadcast(
                sender_id=agent.agent_id,
                message_id=message.message_id,
                subject=request.subject,
                priority=request.priority.value,
            ))
        except Exception as e:
            # Don't fail the broadcast if notification fails
            logger.warning(f"Failed to send SSE broadcast notification: {e}")

        return BroadcastResponse(
            success=True,
            message_id=message.message_id,
            recipients_count=recipients_count,
            delivered_count=0,  # Will be updated asynchronously
        )

    except Exception as e:
        logger.error(f"Error sending broadcast: {e}")
        raise HTTPException(status_code=500, detail="Failed to send broadcast")


# =============================================================================
# RECEIVE ENDPOINTS
# =============================================================================

@router.get(
    "",
    response_model=MessageListResponse,
    summary="Get messages",
    description="Get messages for the authenticated agent (inbox)."
)
async def get_messages(
    status: Optional[MessageStatus] = Query(None, description="Filter by status"),
    priority: Optional[MessagePriority] = Query(None, description="Filter by priority"),
    sender_id: Optional[str] = Query(None, description="Filter by sender"),
    thread_id: Optional[str] = Query(None, description="Filter by thread"),
    include_broadcasts: bool = Query(True, description="Include broadcast messages"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> MessageListResponse:
    """
    Get messages for the authenticated agent.

    - Returns messages sorted by priority (URGENT first), then by date
    - Excludes expired messages unless explicitly included
    - Broadcasts are included by default
    """
    repo = get_message_repository()

    try:
        # Get inbox messages
        messages, total = repo.get_inbox(
            agent_id=agent.agent_id,
            status=status,
            priority=priority,
            include_broadcasts=include_broadcasts,
            page=page,
            page_size=page_size,
        )

        # Apply additional filters
        if sender_id:
            messages = [m for m in messages if m.sender_id == sender_id]
        if thread_id:
            messages = [m for m in messages if m.thread_id == thread_id]

        return MessageListResponse(
            success=True,
            messages=messages,
            total=total,
            page=page,
            page_size=page_size,
            has_more=page * page_size < total,
        )

    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        raise HTTPException(status_code=500, detail="Failed to get messages")


@router.get(
    "/sent",
    response_model=MessageListResponse,
    summary="Get sent messages",
    description="Get messages sent by the authenticated agent."
)
async def get_sent_messages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> MessageListResponse:
    """Get messages sent by the authenticated agent."""
    repo = get_message_repository()

    try:
        messages, total = repo.get_sent(
            agent_id=agent.agent_id,
            page=page,
            page_size=page_size,
        )

        return MessageListResponse(
            success=True,
            messages=messages,
            total=total,
            page=page,
            page_size=page_size,
            has_more=page * page_size < total,
        )

    except Exception as e:
        logger.error(f"Error getting sent messages: {e}")
        raise HTTPException(status_code=500, detail="Failed to get sent messages")


@router.get(
    "/unread-count",
    summary="Get unread message count",
    description="Get count of unread messages for the authenticated agent."
)
async def get_unread_count(
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> dict:
    """Get count of unread messages."""
    repo = get_message_repository()

    try:
        count = repo.get_unread_count(agent.agent_id)
        return {"unread_count": count}

    except Exception as e:
        logger.error(f"Error getting unread count: {e}")
        raise HTTPException(status_code=500, detail="Failed to get unread count")


@router.get(
    "/thread/{thread_id}",
    response_model=MessageListResponse,
    summary="Get thread messages",
    description="Get all messages in a conversation thread."
)
async def get_thread(
    thread_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> MessageListResponse:
    """
    Get all messages in a thread.

    - Messages are sorted chronologically (oldest first)
    - Includes all participants' messages
    """
    repo = get_message_repository()

    try:
        messages, total = repo.get_thread(
            thread_id=thread_id,
            page=page,
            page_size=page_size,
        )

        return MessageListResponse(
            success=True,
            messages=messages,
            total=total,
            page=page,
            page_size=page_size,
            has_more=page * page_size < total,
        )

    except Exception as e:
        logger.error(f"Error getting thread: {e}")
        raise HTTPException(status_code=500, detail="Failed to get thread")


@router.get(
    "/{message_id}",
    response_model=MessageResponse,
    summary="Get message by ID",
    description="Get a specific message by its identifier."
)
async def get_message(
    message_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> MessageResponse:
    """Get a specific message by ID."""
    repo = get_message_repository()

    message = repo.get_by_id(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Check access - must be sender, recipient, or broadcast
    if (message.sender_id != agent.agent_id and
        message.recipient_id != agent.agent_id and
        message.message_type != MessageType.BROADCAST):
        raise HTTPException(status_code=403, detail="Access denied")

    return MessageResponse(success=True, message=message)


# =============================================================================
# STATUS UPDATE ENDPOINTS
# =============================================================================

@router.post(
    "/{message_id}/read",
    response_model=MessageResponse,
    summary="Mark message as read",
    description="Mark a message as read by the authenticated agent."
)
async def mark_read(
    message_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> MessageResponse:
    """Mark a message as read."""
    repo = get_message_repository()

    message = repo.mark_read(message_id, agent.agent_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    return MessageResponse(success=True, message=message)


@router.post(
    "/mark-read-batch",
    summary="Mark multiple messages as read",
    description="Mark multiple messages as read in a single operation."
)
async def mark_read_batch(
    request: MarkReadRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> dict:
    """Mark multiple messages as read."""
    if not request.message_ids:
        raise HTTPException(status_code=400, detail="message_ids required")

    repo = get_message_repository()

    count = repo.mark_multiple_read(request.message_ids, agent.agent_id)

    return {
        "success": True,
        "marked_count": count,
        "requested_count": len(request.message_ids)
    }


# =============================================================================
# DELETE ENDPOINTS
# =============================================================================

@router.delete(
    "/{message_id}",
    summary="Delete message",
    description="Delete a message (soft delete by default)."
)
async def delete_message(
    message_id: str,
    hard_delete: bool = Query(False, description="Permanently delete if True"),
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> dict:
    """
    Delete a message.

    - Soft delete marks message as expired (default)
    - Hard delete permanently removes the message
    - Must be sender or recipient to delete
    """
    repo = get_message_repository()

    success = repo.delete_message(
        message_id=message_id,
        agent_id=agent.agent_id,
        hard_delete=hard_delete,
    )

    if not success:
        raise HTTPException(
            status_code=404,
            detail="Message not found or access denied"
        )

    return {"success": True, "deleted": message_id}


@router.delete(
    "/thread/{thread_id}",
    summary="Delete thread",
    description="Delete all messages in a thread."
)
async def delete_thread(
    thread_id: str,
    hard_delete: bool = Query(False),
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> dict:
    """Delete all messages in a thread."""
    repo = get_message_repository()

    count = repo.delete_thread(
        thread_id=thread_id,
        agent_id=agent.agent_id,
        hard_delete=hard_delete,
    )

    return {"success": True, "deleted_count": count}


# =============================================================================
# STATISTICS ENDPOINTS
# =============================================================================

@router.get(
    "/stats",
    response_model=MessageStats,
    summary="Get message statistics",
    description="Get message statistics for the authenticated agent."
)
async def get_stats(
    agent: AuthenticatedAgent = Depends(get_current_agent),
) -> MessageStats:
    """Get message statistics for the authenticated agent."""
    repo = get_message_repository()

    try:
        stats = repo.get_stats(agent.agent_id)
        return stats

    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get statistics")
