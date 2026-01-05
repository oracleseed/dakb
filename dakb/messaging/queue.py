"""
DAKB Messaging System - Priority Queue

Priority-based message queue implementation with MongoDB backing.
Handles message ordering, delivery attempts, and automatic expiration.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Features:
- Priority-based message ordering (URGENT > HIGH > NORMAL > LOW)
- Automatic message expiration
- Delivery retry with exponential backoff
- Broadcast message handling
- Queue statistics and monitoring
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Callable, Any
from dataclasses import dataclass, field

from pymongo.collection import Collection

from .models import (
    Message,
    MessagePriority,
    MessageStatus,
    QueuedMessage,
    QueueStats,
    NotificationType,
)

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Priority scores for queue ordering (higher = more urgent)
PRIORITY_SCORES = {
    MessagePriority.URGENT: 1000,
    MessagePriority.HIGH: 100,
    MessagePriority.NORMAL: 10,
    MessagePriority.LOW: 1,
}

# Retry delays in seconds (exponential backoff)
RETRY_DELAYS = [30, 60, 120, 300, 600]  # 30s, 1m, 2m, 5m, 10m

# Maximum delivery attempts before giving up
MAX_DELIVERY_ATTEMPTS = 5


# =============================================================================
# QUEUE ITEM
# =============================================================================

@dataclass
class QueueItem:
    """
    In-memory queue item for processing.

    Combines message data with queue metadata for efficient processing.
    """
    message_id: str
    priority: MessagePriority
    priority_score: int
    created_at: datetime
    recipient_id: Optional[str]
    is_broadcast: bool
    attempts: int = 0
    next_attempt_at: Optional[datetime] = None
    last_error: Optional[str] = None

    def __lt__(self, other: 'QueueItem') -> bool:
        """Compare by priority score (higher first) then by age (older first)."""
        if self.priority_score != other.priority_score:
            return self.priority_score > other.priority_score
        return self.created_at < other.created_at


# =============================================================================
# PRIORITY QUEUE
# =============================================================================

class MessageQueue:
    """
    Priority-based message queue with MongoDB persistence.

    Processes messages in priority order, handles retries,
    and manages broadcast delivery.
    """

    def __init__(
        self,
        messages_collection: Collection,
        queue_collection: Collection,
    ):
        """
        Initialize the message queue.

        Args:
            messages_collection: MongoDB collection for messages
            queue_collection: MongoDB collection for queue state
        """
        self.messages = messages_collection
        self.queue = queue_collection
        self._running = False
        self._processing_task: Optional[asyncio.Task] = None

    # =========================================================================
    # QUEUE OPERATIONS
    # =========================================================================

    def enqueue(self, message: Message) -> QueuedMessage:
        """
        Add a message to the priority queue.

        Args:
            message: Message to queue

        Returns:
            Queued message record
        """
        priority_score = PRIORITY_SCORES.get(message.priority, 10)

        queued = QueuedMessage(
            message_id=message.message_id,
            priority=message.priority,
            priority_score=priority_score,
            enqueued_at=datetime.utcnow(),
            attempts=0,
            next_attempt_at=datetime.utcnow(),  # Ready immediately
        )

        # Insert into queue collection
        self.queue.insert_one(queued.model_dump())

        logger.debug(
            f"Message {message.message_id} enqueued with priority "
            f"{message.priority.value} (score: {priority_score})"
        )

        return queued

    def dequeue(self, batch_size: int = 10) -> List[QueuedMessage]:
        """
        Get the next batch of messages ready for processing.

        Returns messages ordered by priority (highest first),
        filtering out those with future next_attempt_at times.

        Args:
            batch_size: Maximum messages to dequeue

        Returns:
            List of queued messages ready for processing
        """
        now = datetime.utcnow()

        # Find messages ready for delivery
        cursor = self.queue.find({
            "$or": [
                {"next_attempt_at": {"$lte": now}},
                {"next_attempt_at": None}
            ],
            "attempts": {"$lt": MAX_DELIVERY_ATTEMPTS}
        }).sort([
            ("priority_score", -1),  # Highest priority first
            ("enqueued_at", 1)       # Then oldest first
        ]).limit(batch_size)

        items = []
        for doc in cursor:
            doc.pop("_id", None)
            items.append(QueuedMessage(**doc))

        return items

    def peek(self, count: int = 5) -> List[QueuedMessage]:
        """
        Peek at the top items in the queue without removing them.

        Args:
            count: Number of items to peek

        Returns:
            List of top queue items
        """
        cursor = self.queue.find().sort([
            ("priority_score", -1),
            ("enqueued_at", 1)
        ]).limit(count)

        items = []
        for doc in cursor:
            doc.pop("_id", None)
            items.append(QueuedMessage(**doc))

        return items

    def remove(self, message_id: str) -> bool:
        """
        Remove a message from the queue (after successful delivery).

        Args:
            message_id: Message identifier

        Returns:
            True if removed, False if not found
        """
        result = self.queue.delete_one({"message_id": message_id})
        if result.deleted_count > 0:
            logger.debug(f"Message {message_id} removed from queue")
            return True
        return False

    def retry(
        self,
        message_id: str,
        error: Optional[str] = None,
    ) -> Optional[QueuedMessage]:
        """
        Schedule a message for retry after failed delivery.

        Uses exponential backoff for retry delays.

        Args:
            message_id: Message identifier
            error: Error message from failed attempt

        Returns:
            Updated queued message or None if max attempts exceeded
        """
        # Get current state
        doc = self.queue.find_one({"message_id": message_id})
        if not doc:
            return None

        current_attempts = doc.get("attempts", 0)
        new_attempts = current_attempts + 1

        # Check if max attempts exceeded
        if new_attempts >= MAX_DELIVERY_ATTEMPTS:
            logger.warning(
                f"Message {message_id} exceeded max delivery attempts ({MAX_DELIVERY_ATTEMPTS})"
            )
            # Mark message as expired
            self.messages.update_one(
                {"message_id": message_id},
                {"$set": {"status": MessageStatus.EXPIRED.value}}
            )
            self.remove(message_id)
            return None

        # Calculate next retry time with exponential backoff
        delay_index = min(new_attempts - 1, len(RETRY_DELAYS) - 1)
        delay_seconds = RETRY_DELAYS[delay_index]
        next_attempt = datetime.utcnow() + timedelta(seconds=delay_seconds)

        # Update queue entry
        result = self.queue.find_one_and_update(
            {"message_id": message_id},
            {
                "$set": {
                    "attempts": new_attempts,
                    "next_attempt_at": next_attempt,
                    "last_error": error
                }
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(
                f"Message {message_id} scheduled for retry {new_attempts}/{MAX_DELIVERY_ATTEMPTS} "
                f"at {next_attempt.isoformat()} (delay: {delay_seconds}s)"
            )
            return QueuedMessage(**result)

        return None

    # =========================================================================
    # BROADCAST HANDLING
    # =========================================================================

    def get_broadcast_recipients(self, exclude_sender: str) -> List[str]:
        """
        Get list of all registered agents for broadcast delivery.

        This should be called with the agents collection to get recipient list.

        Args:
            exclude_sender: Sender to exclude from recipients

        Returns:
            List of agent IDs
        """
        # This is a placeholder - actual implementation should query agents collection
        # The actual recipient list comes from the AgentRepository
        logger.debug(f"Getting broadcast recipients (excluding {exclude_sender})")
        return []

    def mark_broadcast_delivered(
        self,
        message_id: str,
        agent_id: str,
    ) -> bool:
        """
        Mark a broadcast message as delivered to a specific agent.

        Broadcasts are tracked separately since they have multiple recipients.

        Args:
            message_id: Broadcast message ID
            agent_id: Recipient agent ID

        Returns:
            True if marked, False if already marked or error
        """
        from .models import DeliveryReceipt

        receipt = DeliveryReceipt(
            agent_id=agent_id,
            delivery_method=NotificationType.POLLING
        )

        result = self.messages.update_one(
            {
                "message_id": message_id,
                "delivery_receipts.agent_id": {"$ne": agent_id}
            },
            {
                "$push": {"delivery_receipts": receipt.model_dump()},
                "$set": {"status": MessageStatus.DELIVERED.value}
            }
        )

        return result.modified_count > 0

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_stats(self) -> QueueStats:
        """
        Get queue statistics.

        Returns:
            Queue statistics
        """
        # Total pending
        total_pending = self.queue.count_documents({})

        # By priority
        priority_pipeline = [
            {"$group": {"_id": "$priority", "count": {"$sum": 1}}}
        ]
        by_priority = {}
        for doc in self.queue.aggregate(priority_pipeline):
            if doc["_id"]:
                by_priority[doc["_id"]] = doc["count"]

        # Oldest message age
        oldest = self.queue.find_one(
            {},
            sort=[("enqueued_at", 1)]
        )
        oldest_age = None
        if oldest:
            oldest_age = (datetime.utcnow() - oldest["enqueued_at"]).total_seconds()

        return QueueStats(
            total_pending=total_pending,
            by_priority=by_priority,
            oldest_message_age_seconds=oldest_age,
            processing_rate_per_minute=0.0  # TODO: Implement rate tracking
        )

    def get_queue_depth_by_priority(self) -> dict[str, int]:
        """
        Get queue depth broken down by priority.

        Returns:
            Dictionary mapping priority to count
        """
        result = {p.value: 0 for p in MessagePriority}

        pipeline = [
            {"$group": {"_id": "$priority", "count": {"$sum": 1}}}
        ]

        for doc in self.queue.aggregate(pipeline):
            if doc["_id"] and doc["_id"] in result:
                result[doc["_id"]] = doc["count"]

        return result

    # =========================================================================
    # CLEANUP
    # =========================================================================

    def cleanup_stale(self, older_than_hours: int = 24) -> int:
        """
        Remove stale queue entries (messages that failed all retries
        or were somehow orphaned).

        Args:
            older_than_hours: Remove entries older than this

        Returns:
            Number of entries removed
        """
        cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)

        result = self.queue.delete_many({
            "$or": [
                {
                    "enqueued_at": {"$lt": cutoff},
                    "attempts": {"$gte": MAX_DELIVERY_ATTEMPTS}
                },
                {
                    "enqueued_at": {"$lt": cutoff - timedelta(hours=24)},
                    # Orphaned entries older than 48 hours
                }
            ]
        })

        if result.deleted_count > 0:
            logger.info(f"Cleaned up {result.deleted_count} stale queue entries")

        return result.deleted_count

    def requeue_stuck(self, stuck_threshold_minutes: int = 30) -> int:
        """
        Requeue messages that appear stuck (no progress for threshold time).

        Args:
            stuck_threshold_minutes: Consider stuck if no progress after this time

        Returns:
            Number of messages requeued
        """
        threshold = datetime.utcnow() - timedelta(minutes=stuck_threshold_minutes)

        result = self.queue.update_many(
            {
                "next_attempt_at": {"$lt": threshold},
                "attempts": {"$lt": MAX_DELIVERY_ATTEMPTS}
            },
            {
                "$set": {"next_attempt_at": datetime.utcnow()},
                "$inc": {"attempts": 1}
            }
        )

        if result.modified_count > 0:
            logger.info(f"Requeued {result.modified_count} stuck messages")

        return result.modified_count


# =============================================================================
# ASYNC QUEUE PROCESSOR
# =============================================================================

class AsyncQueueProcessor:
    """
    Asynchronous queue processor for background message delivery.

    Runs as a background task, continuously processing the message queue
    and handling delivery to recipients.
    """

    def __init__(
        self,
        queue: MessageQueue,
        delivery_callback: Optional[Callable[[Message, str], bool]] = None,
        batch_size: int = 10,
        poll_interval_seconds: float = 1.0,
    ):
        """
        Initialize the async queue processor.

        Args:
            queue: Message queue instance
            delivery_callback: Function to call for message delivery
            batch_size: Number of messages to process per batch
            poll_interval_seconds: Time between queue polls
        """
        self.queue = queue
        self.delivery_callback = delivery_callback
        self.batch_size = batch_size
        self.poll_interval = poll_interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._processed_count = 0
        self._error_count = 0

    async def start(self) -> None:
        """Start the queue processor."""
        if self._running:
            logger.warning("Queue processor already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info("Async queue processor started")

    async def stop(self) -> None:
        """Stop the queue processor gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(
            f"Async queue processor stopped. "
            f"Processed: {self._processed_count}, Errors: {self._error_count}"
        )

    async def _process_loop(self) -> None:
        """Main processing loop."""
        while self._running:
            try:
                # Get batch of messages to process
                items = self.queue.dequeue(self.batch_size)

                if items:
                    await self._process_batch(items)
                else:
                    # No messages, wait before next poll
                    await asyncio.sleep(self.poll_interval)

            except Exception as e:
                logger.error(f"Error in queue processing loop: {e}")
                self._error_count += 1
                await asyncio.sleep(self.poll_interval)

    async def _process_batch(self, items: List[QueuedMessage]) -> None:
        """
        Process a batch of queued messages.

        Args:
            items: Queued messages to process
        """
        for item in items:
            if not self._running:
                break

            try:
                success = await self._process_item(item)

                if success:
                    self.queue.remove(item.message_id)
                    self._processed_count += 1
                else:
                    self.queue.retry(item.message_id, "Delivery failed")
                    self._error_count += 1

            except Exception as e:
                logger.error(f"Error processing message {item.message_id}: {e}")
                self.queue.retry(item.message_id, str(e))
                self._error_count += 1

    async def _process_item(self, item: QueuedMessage) -> bool:
        """
        Process a single queue item.

        Args:
            item: Queue item to process

        Returns:
            True if successful, False otherwise
        """
        # Get full message from database
        doc = self.queue.messages.find_one({"message_id": item.message_id})
        if not doc:
            logger.warning(f"Message {item.message_id} not found in database")
            return True  # Remove from queue since message doesn't exist

        doc.pop("_id", None)
        message = Message(**doc)

        # Check if expired
        if message.is_expired():
            logger.debug(f"Message {item.message_id} expired, removing from queue")
            self.queue.messages.update_one(
                {"message_id": item.message_id},
                {"$set": {"status": MessageStatus.EXPIRED.value}}
            )
            return True

        # Use delivery callback if provided
        if self.delivery_callback:
            try:
                recipient = message.recipient_id or "broadcast"
                return self.delivery_callback(message, recipient)
            except Exception as e:
                logger.error(f"Delivery callback error: {e}")
                return False

        # Default: just mark as delivered (polling model)
        self.queue.messages.update_one(
            {"message_id": item.message_id},
            {
                "$set": {
                    "status": MessageStatus.DELIVERED.value,
                    "delivered_at": datetime.utcnow()
                }
            }
        )
        return True

    @property
    def stats(self) -> dict[str, Any]:
        """Get processor statistics."""
        return {
            "running": self._running,
            "processed_count": self._processed_count,
            "error_count": self._error_count,
            "queue_stats": self.queue.get_stats().model_dump() if self.queue else None
        }
