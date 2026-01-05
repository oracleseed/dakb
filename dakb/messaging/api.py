"""
DAKB Messaging System - API

Repository class for message CRUD operations with MongoDB.
Handles message storage, retrieval, filtering, and status updates.

Version: 1.2
Created: 2025-12-08
Updated: 2025-12-11
Author: Backend Agent (Claude Opus 4.5)

Features:
- Send direct and broadcast messages
- Retrieve messages with filtering
- Mark messages as delivered/read
- Delete messages (soft and hard delete)
- Thread management
- Message statistics
- Alias resolution for message routing (Phase 3 Token Alias System)
- Shared inbox for token teams (Phase 4 Token Alias System)
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Any, Callable

from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from .models import (
    Message,
    MessageCreate,
    MessageFilter,
    MessageType,
    MessagePriority,
    MessageStatus,
    NotificationType,
    DeliveryReceipt,
    ReadReceipt,
    MessageStats,
    generate_message_id,
    generate_thread_id,
)

logger = logging.getLogger(__name__)


class MessageRepository:
    """
    Repository for DAKB message operations.

    Handles all message CRUD operations including sending, receiving,
    status updates, and filtering.

    Phase 3 Token Alias System:
    - Supports optional alias_resolver callback for recipient alias resolution
    - When a message is sent to an alias, resolves to the owning token_id
    - Preserves original_recipient for audit trail and debugging

    Phase 4 Token Alias System (Shared Inbox):
    - Supports optional alias_lister callback for inbox queries
    - Inbox queries return messages sent to token_id OR any of its aliases
    - All team members using aliases share a single inbox
    """

    def __init__(
        self,
        collection: Collection,
        alias_resolver: Optional[Callable[[str], Optional[str]]] = None,
        alias_lister: Optional[Callable[[str], List[str]]] = None
    ):
        """
        Initialize message repository.

        Args:
            collection: MongoDB collection for dakb_messages
            alias_resolver: Optional callback to resolve aliases to token_ids.
                           Signature: (alias: str) -> Optional[str]
                           Returns token_id if alias exists, None otherwise.
            alias_lister: Optional callback to get all aliases for a token_id.
                         Signature: (token_id: str) -> List[str]
                         Returns list of alias names for the token.
                         Used by Phase 4 shared inbox functionality.
        """
        self.collection = collection
        self._alias_resolver = alias_resolver
        self._alias_lister = alias_lister

    def set_alias_resolver(self, resolver: Callable[[str], Optional[str]]) -> None:
        """
        Set or update the alias resolver callback.

        This allows late-binding of the resolver after repository construction,
        useful when repositories are created before the alias repository is available.

        Args:
            resolver: Callback to resolve aliases to token_ids.
        """
        self._alias_resolver = resolver
        logger.debug("Alias resolver set for MessageRepository")

    def set_alias_lister(self, lister: Callable[[str], List[str]]) -> None:
        """
        Set or update the alias lister callback.

        Phase 4 Token Alias System:
        This allows late-binding of the lister after repository construction,
        useful when repositories are created before the alias repository is available.

        Args:
            lister: Callback to get all alias names for a token_id.
        """
        self._alias_lister = lister
        logger.debug("Alias lister set for MessageRepository")

    def _get_aliases_for_token(self, token_id: str) -> List[str]:
        """
        Get all alias names for a token_id.

        Phase 4 Token Alias System:
        Used by get_inbox() to build shared inbox query.

        Args:
            token_id: The token identifier to get aliases for

        Returns:
            List of alias names, empty list if no aliases or no lister
        """
        if self._alias_lister is None:
            return []

        try:
            aliases = self._alias_lister(token_id)
            if aliases:
                logger.debug(
                    f"Retrieved {len(aliases)} aliases for token '{token_id}': {aliases}"
                )
            return aliases
        except Exception as e:
            logger.warning(
                f"Error getting aliases for token '{token_id}': {e}. "
                "Returning empty list."
            )
            return []

    def _resolve_recipient(self, recipient_id: str) -> tuple[str, Optional[str]]:
        """
        Resolve a recipient ID, checking if it's an alias.

        Phase 3 Token Alias System:
        - If recipient_id is an alias, returns (resolved_token_id, original_alias)
        - If not an alias (or no resolver), returns (recipient_id, None)

        Args:
            recipient_id: The recipient identifier (could be alias or direct token_id)

        Returns:
            Tuple of (actual_recipient, original_recipient):
            - actual_recipient: The token_id to route the message to
            - original_recipient: The alias used (if resolved), None otherwise
        """
        if not recipient_id:
            # Broadcast message - no resolution needed
            return (recipient_id, None)

        if self._alias_resolver is None:
            # No alias resolver configured - use recipient_id as-is
            return (recipient_id, None)

        try:
            resolved_token_id = self._alias_resolver(recipient_id)

            if resolved_token_id is not None:
                # It was an alias - resolved to token_id
                logger.debug(
                    f"Alias '{recipient_id}' resolved to token '{resolved_token_id}'"
                )
                return (resolved_token_id, recipient_id)
            else:
                # Not an alias - use as direct token_id
                logger.debug(
                    f"Recipient '{recipient_id}' is not an alias, using as direct token_id"
                )
                return (recipient_id, None)

        except Exception as e:
            # Error in alias resolution - fail gracefully, use original
            logger.warning(
                f"Error resolving alias '{recipient_id}': {e}. "
                "Using as direct token_id."
            )
            return (recipient_id, None)

    # =========================================================================
    # SEND OPERATIONS
    # =========================================================================

    def send_message(
        self,
        sender_id: str,
        data: MessageCreate,
    ) -> Message:
        """
        Send a new message.

        Phase 3 Token Alias System:
        - If recipient_id is an alias, resolves to the owning token_id
        - Stores original_recipient to preserve the alias used for audit trail
        - Backwards compatible: direct token_ids continue to work unchanged

        Args:
            sender_id: Sending agent ID
            data: Message creation data

        Returns:
            Created message

        Raises:
            ValueError: If validation fails
        """
        # Calculate expiration
        expires_at = datetime.utcnow() + timedelta(hours=data.expires_in_hours)

        # Generate thread ID if this starts a new thread
        thread_id = data.thread_id
        if thread_id is None and data.reply_to_id is None:
            thread_id = generate_thread_id()

        # If replying, get thread from parent message
        if data.reply_to_id and not thread_id:
            parent = self.get_by_id(data.reply_to_id)
            if parent:
                thread_id = parent.thread_id

        # Phase 3: Resolve recipient alias if applicable
        actual_recipient = data.recipient_id
        original_recipient = None

        if data.recipient_id:
            actual_recipient, original_recipient = self._resolve_recipient(data.recipient_id)

        # Create message with resolved recipient
        message = Message(
            message_id=generate_message_id(),
            sender_id=sender_id,
            recipient_id=actual_recipient,
            original_recipient=original_recipient,
            message_type=data.message_type,
            priority=data.priority,
            subject=data.subject,
            content=data.content,
            attachments=data.attachments,
            thread_id=thread_id,
            reply_to_id=data.reply_to_id,
            status=MessageStatus.PENDING,
            expires_at=expires_at,
            metadata=data.metadata,
        )

        # Insert into MongoDB
        doc = message.model_dump()
        self.collection.insert_one(doc)

        # Log with alias resolution info if applicable
        if original_recipient:
            logger.info(
                f"Message sent: {message.message_id} from {sender_id} "
                f"to alias '{original_recipient}' (resolved to '{actual_recipient}') "
                f"(priority: {data.priority.value})"
            )
        else:
            logger.info(
                f"Message sent: {message.message_id} from {sender_id} "
                f"to {actual_recipient or 'broadcast'} (priority: {data.priority.value})"
            )

        return message

    def send_broadcast(
        self,
        sender_id: str,
        subject: str,
        content: str,
        priority: MessagePriority = MessagePriority.NORMAL,
        expires_in_hours: int = 168,
        metadata: Optional[dict] = None,
    ) -> Message:
        """
        Send a broadcast message to all agents.

        Args:
            sender_id: Sending agent ID
            subject: Message subject
            content: Message body
            priority: Message priority
            expires_in_hours: Hours until expiration
            metadata: Additional metadata

        Returns:
            Created broadcast message
        """
        data = MessageCreate(
            recipient_id=None,  # None indicates broadcast
            message_type=MessageType.BROADCAST,
            priority=priority,
            subject=subject,
            content=content,
            expires_in_hours=expires_in_hours,
            metadata=metadata or {},
        )

        return self.send_message(sender_id, data)

    def send_system_message(
        self,
        recipient_id: str,
        subject: str,
        content: str,
        priority: MessagePriority = MessagePriority.HIGH,
    ) -> Message:
        """
        Send a system-generated message.

        Args:
            recipient_id: Target agent ID
            subject: Message subject
            content: Message body
            priority: Message priority

        Returns:
            Created system message
        """
        data = MessageCreate(
            recipient_id=recipient_id,
            message_type=MessageType.SYSTEM,
            priority=priority,
            subject=subject,
            content=content,
            expires_in_hours=168,  # 7 days
        )

        return self.send_message("system", data)

    # =========================================================================
    # RETRIEVE OPERATIONS
    # =========================================================================

    def get_by_id(self, message_id: str) -> Optional[Message]:
        """
        Get a message by ID.

        Args:
            message_id: Message identifier

        Returns:
            Message or None if not found
        """
        doc = self.collection.find_one({"message_id": message_id})
        if doc:
            doc.pop("_id", None)
            return Message(**doc)
        return None

    def get_messages(
        self,
        filter_criteria: MessageFilter,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Message], int]:
        """
        Get messages with filtering and pagination.

        Args:
            filter_criteria: Filter criteria
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (messages list, total count)
        """
        query = self._build_query(filter_criteria)

        # Get total count
        total = self.collection.count_documents(query)

        # Calculate skip
        skip = (page - 1) * page_size

        # Query with sort (priority_score desc, then created_at desc)
        # ISS-071 Fix: Use priority_score instead of priority string for correct sorting
        cursor = self.collection.find(query).sort([
            ("priority_score", -1),  # URGENT (1000) first
            ("created_at", -1)  # Newest first within priority
        ]).skip(skip).limit(page_size)

        messages = []
        for doc in cursor:
            doc.pop("_id", None)
            messages.append(Message(**doc))

        return messages, total

    def get_inbox(
        self,
        agent_id: str,
        status: Optional[MessageStatus] = None,
        priority: Optional[MessagePriority] = None,
        include_broadcasts: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Message], int]:
        """
        Get messages for an agent's inbox.

        Phase 4 Token Alias System (Shared Inbox):
        This method now supports shared inbox for token teams. When an agent_id
        (token_id) has registered aliases, the inbox will include messages sent to:
        - The token_id directly
        - Any of the token's aliases

        This means all team members using aliases share a single inbox.

        Historical messages: Messages sent to aliases that were later deactivated
        will still appear in the inbox since they were routed to the token_id
        at send time. The original_recipient field preserves which alias was used.

        Args:
            agent_id: Target agent ID (token_id)
            status: Filter by status
            priority: Filter by priority
            include_broadcasts: Include broadcast messages
            page: Page number
            page_size: Items per page

        Returns:
            Tuple of (messages list, total count)
        """
        # Phase 4: Get all aliases for this token to build shared inbox query
        aliases = self._get_aliases_for_token(agent_id)

        # Build list of all recipient_ids to match (token + all aliases)
        recipient_ids = [agent_id] + aliases

        # Build query for inbox (messages to this token, any alias, or broadcasts)
        if include_broadcasts:
            query: dict[str, Any] = {
                "$or": [
                    {"recipient_id": {"$in": recipient_ids}},
                    {"message_type": MessageType.BROADCAST.value}
                ]
            }
        else:
            query = {"recipient_id": {"$in": recipient_ids}}

        # Exclude expired unless explicitly included
        query["$and"] = query.get("$and", [])
        query["$and"].append({
            "$or": [
                {"expires_at": {"$gt": datetime.utcnow()}},
                {"status": MessageStatus.READ.value}  # Keep read messages
            ]
        })

        if status:
            query["status"] = status.value
        if priority:
            query["priority"] = priority.value

        total = self.collection.count_documents(query)
        skip = (page - 1) * page_size

        # ISS-071 Fix: Use priority_score for correct sorting
        cursor = self.collection.find(query).sort([
            ("priority_score", -1),
            ("created_at", -1)
        ]).skip(skip).limit(page_size)

        messages = []
        for doc in cursor:
            doc.pop("_id", None)
            messages.append(Message(**doc))

        return messages, total

    def get_sent(
        self,
        agent_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Message], int]:
        """
        Get messages sent by an agent.

        Args:
            agent_id: Sender agent ID
            page: Page number
            page_size: Items per page

        Returns:
            Tuple of (messages list, total count)
        """
        query = {"sender_id": agent_id}

        total = self.collection.count_documents(query)
        skip = (page - 1) * page_size

        cursor = self.collection.find(query).sort(
            "created_at", -1
        ).skip(skip).limit(page_size)

        messages = []
        for doc in cursor:
            doc.pop("_id", None)
            messages.append(Message(**doc))

        return messages, total

    def get_thread(
        self,
        thread_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[List[Message], int]:
        """
        Get all messages in a thread.

        Args:
            thread_id: Thread identifier
            page: Page number
            page_size: Items per page

        Returns:
            Tuple of (messages list, total count)
        """
        query = {"thread_id": thread_id}

        total = self.collection.count_documents(query)
        skip = (page - 1) * page_size

        # Sort by created_at ascending for thread context
        cursor = self.collection.find(query).sort(
            "created_at", 1
        ).skip(skip).limit(page_size)

        messages = []
        for doc in cursor:
            doc.pop("_id", None)
            messages.append(Message(**doc))

        return messages, total

    def get_unread_count(self, agent_id: str) -> int:
        """
        Get count of unread messages for an agent.

        Phase 4 Token Alias System (Shared Inbox):
        Includes messages sent to the token_id or any of its aliases.

        Args:
            agent_id: Agent identifier (token_id)

        Returns:
            Number of unread messages
        """
        # Phase 4: Get all aliases for this token to build shared inbox query
        aliases = self._get_aliases_for_token(agent_id)
        recipient_ids = [agent_id] + aliases

        query = {
            "$or": [
                {"recipient_id": {"$in": recipient_ids}},
                {"message_type": MessageType.BROADCAST.value}
            ],
            "status": {"$in": [MessageStatus.PENDING.value, MessageStatus.DELIVERED.value]},
            "expires_at": {"$gt": datetime.utcnow()}
        }
        return self.collection.count_documents(query)

    # =========================================================================
    # STATUS UPDATE OPERATIONS
    # =========================================================================

    def mark_delivered(
        self,
        message_id: str,
        agent_id: str,
        method: NotificationType = NotificationType.POLLING,
    ) -> Optional[Message]:
        """
        Mark a message as delivered to an agent.

        Args:
            message_id: Message identifier
            agent_id: Receiving agent ID
            method: Delivery method used

        Returns:
            Updated message or None if not found
        """
        receipt = DeliveryReceipt(
            agent_id=agent_id,
            delivery_method=method
        )

        result = self.collection.find_one_and_update(
            {
                "message_id": message_id,
                "delivery_receipts.agent_id": {"$ne": agent_id}  # Prevent duplicates
            },
            {
                "$push": {"delivery_receipts": receipt.model_dump()},
                "$set": {
                    "status": MessageStatus.DELIVERED.value,
                    "delivered_at": datetime.utcnow()
                }
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.debug(f"Message {message_id} marked delivered to {agent_id}")
            return Message(**result)
        return None

    def mark_read(
        self,
        message_id: str,
        agent_id: str,
    ) -> Optional[Message]:
        """
        Mark a message as read by an agent.

        Args:
            message_id: Message identifier
            agent_id: Reading agent ID

        Returns:
            Updated message or None if not found
        """
        receipt = ReadReceipt(agent_id=agent_id)

        result = self.collection.find_one_and_update(
            {
                "message_id": message_id,
                "read_receipts.agent_id": {"$ne": agent_id}  # Prevent duplicates
            },
            {
                "$push": {"read_receipts": receipt.model_dump()},
                "$set": {
                    "status": MessageStatus.READ.value,
                    "read_at": datetime.utcnow()
                }
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.debug(f"Message {message_id} marked read by {agent_id}")
            return Message(**result)

        # May already be read - just return the message
        return self.get_by_id(message_id)

    def mark_multiple_read(
        self,
        message_ids: List[str],
        agent_id: str,
    ) -> int:
        """
        Mark multiple messages as read.

        Args:
            message_ids: List of message identifiers
            agent_id: Reading agent ID

        Returns:
            Number of messages marked read
        """
        receipt = ReadReceipt(agent_id=agent_id)

        result = self.collection.update_many(
            {
                "message_id": {"$in": message_ids},
                "read_receipts.agent_id": {"$ne": agent_id}
            },
            {
                "$push": {"read_receipts": receipt.model_dump()},
                "$set": {
                    "status": MessageStatus.READ.value,
                    "read_at": datetime.utcnow()
                }
            }
        )

        logger.info(f"Marked {result.modified_count} messages as read for {agent_id}")
        return result.modified_count

    # =========================================================================
    # DELETE OPERATIONS
    # =========================================================================

    def delete_message(
        self,
        message_id: str,
        agent_id: str,
        hard_delete: bool = False,
    ) -> bool:
        """
        Delete a message.

        Args:
            message_id: Message identifier
            agent_id: Agent requesting deletion (must be sender or recipient)
            hard_delete: If True, permanently remove; if False, mark expired

        Returns:
            True if deleted, False if not found or unauthorized
        """
        # Verify ownership
        message = self.get_by_id(message_id)
        if not message:
            return False

        if message.sender_id != agent_id and message.recipient_id != agent_id:
            logger.warning(
                f"Unauthorized delete attempt: {agent_id} tried to delete "
                f"message from {message.sender_id} to {message.recipient_id}"
            )
            return False

        if hard_delete:
            result = self.collection.delete_one({"message_id": message_id})
            success = result.deleted_count > 0
        else:
            # Soft delete by expiring immediately
            result = self.collection.update_one(
                {"message_id": message_id},
                {
                    "$set": {
                        "status": MessageStatus.EXPIRED.value,
                        "expires_at": datetime.utcnow()
                    }
                }
            )
            success = result.modified_count > 0

        if success:
            logger.info(f"Message {message_id} deleted by {agent_id} (hard={hard_delete})")

        return success

    def delete_thread(
        self,
        thread_id: str,
        agent_id: str,
        hard_delete: bool = False,
    ) -> int:
        """
        Delete all messages in a thread.

        Args:
            thread_id: Thread identifier
            agent_id: Agent requesting deletion
            hard_delete: If True, permanently remove

        Returns:
            Number of messages deleted
        """
        if hard_delete:
            result = self.collection.delete_many({
                "thread_id": thread_id,
                "$or": [
                    {"sender_id": agent_id},
                    {"recipient_id": agent_id}
                ]
            })
            count = result.deleted_count
        else:
            result = self.collection.update_many(
                {
                    "thread_id": thread_id,
                    "$or": [
                        {"sender_id": agent_id},
                        {"recipient_id": agent_id}
                    ]
                },
                {
                    "$set": {
                        "status": MessageStatus.EXPIRED.value,
                        "expires_at": datetime.utcnow()
                    }
                }
            )
            count = result.modified_count

        logger.info(f"Deleted {count} messages in thread {thread_id} for {agent_id}")
        return count

    # =========================================================================
    # STATISTICS & CLEANUP
    # =========================================================================

    def get_stats(self, agent_id: str) -> MessageStats:
        """
        Get message statistics for an agent.

        Phase 4 Token Alias System (Shared Inbox):
        Includes messages sent to the token_id or any of its aliases.

        Args:
            agent_id: Agent identifier (token_id)

        Returns:
            Message statistics
        """
        # Phase 4: Get all aliases for this token to build shared inbox query
        aliases = self._get_aliases_for_token(agent_id)
        recipient_ids = [agent_id] + aliases

        # Count sent messages
        total_sent = self.collection.count_documents({"sender_id": agent_id})

        # Count received messages (including messages to any alias)
        total_received = self.collection.count_documents({
            "$or": [
                {"recipient_id": {"$in": recipient_ids}},
                {"message_type": MessageType.BROADCAST.value}
            ]
        })

        # Count unread (already updated for Phase 4)
        unread_count = self.get_unread_count(agent_id)

        # Count pending (messages waiting to be delivered)
        pending_count = self.collection.count_documents({
            "sender_id": agent_id,
            "status": MessageStatus.PENDING.value
        })

        # Group by priority (including messages to any alias)
        priority_pipeline = [
            {"$match": {
                "$or": [
                    {"recipient_id": {"$in": recipient_ids}},
                    {"message_type": MessageType.BROADCAST.value}
                ]
            }},
            {"$group": {"_id": "$priority", "count": {"$sum": 1}}}
        ]
        by_priority = {}
        for doc in self.collection.aggregate(priority_pipeline):
            if doc["_id"]:
                by_priority[doc["_id"]] = doc["count"]

        # Group by type (including messages to any alias)
        type_pipeline = [
            {"$match": {
                "$or": [
                    {"recipient_id": {"$in": recipient_ids}},
                    {"message_type": MessageType.BROADCAST.value}
                ]
            }},
            {"$group": {"_id": "$message_type", "count": {"$sum": 1}}}
        ]
        by_type = {}
        for doc in self.collection.aggregate(type_pipeline):
            if doc["_id"]:
                by_type[doc["_id"]] = doc["count"]

        return MessageStats(
            agent_id=agent_id,
            total_sent=total_sent,
            total_received=total_received,
            unread_count=unread_count,
            pending_count=pending_count,
            by_priority=by_priority,
            by_type=by_type,
        )

    def cleanup_expired(self, before: Optional[datetime] = None) -> int:
        """
        Mark expired messages.

        Args:
            before: Consider expired if expires_at is before this time

        Returns:
            Number of messages marked expired
        """
        if before is None:
            before = datetime.utcnow()

        result = self.collection.update_many(
            {
                "expires_at": {"$lt": before},
                "status": {"$ne": MessageStatus.EXPIRED.value}
            },
            {"$set": {"status": MessageStatus.EXPIRED.value}}
        )

        if result.modified_count > 0:
            logger.info(f"Marked {result.modified_count} messages as expired")

        return result.modified_count

    def purge_expired(self, older_than_days: int = 30) -> int:
        """
        Permanently delete expired messages older than specified days.

        Args:
            older_than_days: Delete expired messages older than this

        Returns:
            Number of messages deleted
        """
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)

        result = self.collection.delete_many({
            "status": MessageStatus.EXPIRED.value,
            "expires_at": {"$lt": cutoff}
        })

        if result.deleted_count > 0:
            logger.info(f"Purged {result.deleted_count} expired messages")

        return result.deleted_count

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _build_query(self, filter_criteria: MessageFilter) -> dict[str, Any]:
        """
        Build MongoDB query from filter criteria.

        Args:
            filter_criteria: Filter criteria

        Returns:
            MongoDB query dictionary
        """
        query: dict[str, Any] = {}

        if filter_criteria.sender_id:
            query["sender_id"] = filter_criteria.sender_id

        if filter_criteria.recipient_id:
            query["recipient_id"] = filter_criteria.recipient_id

        if filter_criteria.message_type:
            query["message_type"] = filter_criteria.message_type.value

        if filter_criteria.priority:
            query["priority"] = filter_criteria.priority.value

        if filter_criteria.status:
            query["status"] = filter_criteria.status.value

        if filter_criteria.thread_id:
            query["thread_id"] = filter_criteria.thread_id

        # Time filters
        time_query = {}
        if filter_criteria.since:
            time_query["$gte"] = filter_criteria.since
        if filter_criteria.before:
            time_query["$lte"] = filter_criteria.before
        if time_query:
            query["created_at"] = time_query

        # Expiration filter
        if not filter_criteria.include_expired:
            query["$or"] = query.get("$or", [])
            query["$or"].append({"expires_at": {"$gt": datetime.utcnow()}})
            query["$or"].append({"status": MessageStatus.READ.value})
            if not query["$or"]:
                del query["$or"]

        return query
