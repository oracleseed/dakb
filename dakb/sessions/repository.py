"""
DAKB Session Management - Repository

MongoDB repository for session CRUD operations including session lifecycle,
context snapshots, and handoff tracking.

Version: 1.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Features:
- Session CRUD with lifecycle management
- Auto-timeout detection and cleanup
- Session chain tracking for handoffs
- Context snapshot storage and retrieval
- Statistics and analytics
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Any

from pymongo.collection import Collection
from pymongo.errors import PyMongoError, DuplicateKeyError

from .models import (
    Session,
    SessionCreate,
    SessionUpdate,
    SessionStatus,
    SessionMetadata,
    SessionChainEntry,
    SessionStats,
    GitContextSnapshot,
    PatchBundle,
    HandoffRequest,
    HandoffPackage,
    HandoffStatus,
    generate_session_id,
    generate_handoff_id,
)

logger = logging.getLogger(__name__)


class SessionRepository:
    """
    Repository for DAKB session operations.

    Handles all session CRUD operations including lifecycle management,
    timeout detection, and handoff tracking.
    """

    def __init__(self, collection: Collection):
        """
        Initialize session repository.

        Args:
            collection: MongoDB collection for dakb_sessions
        """
        self.collection = collection

    # =========================================================================
    # CREATE OPERATIONS
    # =========================================================================

    def create_session(
        self,
        data: SessionCreate,
    ) -> Session:
        """
        Create a new session.

        Args:
            data: Session creation data

        Returns:
            Created session

        Raises:
            ValueError: If validation fails
        """
        # Build metadata
        metadata = SessionMetadata(
            working_directory=data.working_directory,
            task_description=data.task_description,
            loaded_contexts=data.loaded_contexts,
            working_files=data.working_files,
        )

        # Create session
        session = Session(
            session_id=generate_session_id(),
            agent_id=data.agent_id,
            machine_id=data.machine_id,
            agent_type=data.agent_type,
            status=SessionStatus.ACTIVE,
            metadata=metadata,
            timeout_minutes=data.timeout_minutes,
            parent_session_id=data.parent_session_id,
        )

        # If this is a continuation, track original session
        if data.parent_session_id:
            parent = self.get_by_id(data.parent_session_id)
            if parent:
                # Inherit original session ID
                session.original_session_id = parent.original_session_id or parent.session_id
                # Copy session chain and add parent
                session.session_chain = parent.session_chain.copy()
                session.session_chain.append(SessionChainEntry(
                    session_id=parent.session_id,
                    agent_id=parent.agent_id,
                    machine_id=parent.machine_id,
                    status=parent.status,
                    started_at=parent.started_at,
                    ended_at=datetime.utcnow(),
                    handoff_notes=parent.handoff_notes,
                ))

        # Insert into MongoDB
        doc = session.model_dump()
        self.collection.insert_one(doc)

        logger.info(
            f"Session created: {session.session_id} for agent {data.agent_id} "
            f"on {data.machine_id}"
        )

        return session

    # =========================================================================
    # READ OPERATIONS
    # =========================================================================

    def get_by_id(self, session_id: str) -> Optional[Session]:
        """
        Get a session by ID.

        Args:
            session_id: Session identifier

        Returns:
            Session or None if not found
        """
        doc = self.collection.find_one({"session_id": session_id})
        if doc:
            doc.pop("_id", None)
            return Session(**doc)
        return None

    def get_active_sessions(
        self,
        agent_id: Optional[str] = None,
        machine_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Session], int]:
        """
        Get active sessions with optional filtering.

        Args:
            agent_id: Filter by agent ID
            machine_id: Filter by machine ID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (sessions list, total count)
        """
        query: dict[str, Any] = {
            "status": {"$in": [SessionStatus.ACTIVE.value, SessionStatus.RESUMED.value]}
        }

        if agent_id:
            query["agent_id"] = agent_id
        if machine_id:
            query["machine_id"] = machine_id

        total = self.collection.count_documents(query)
        skip = (page - 1) * page_size

        cursor = self.collection.find(query).sort(
            "last_active_at", -1
        ).skip(skip).limit(page_size)

        sessions = []
        for doc in cursor:
            doc.pop("_id", None)
            sessions.append(Session(**doc))

        return sessions, total

    def get_sessions_by_status(
        self,
        status: SessionStatus,
        agent_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[Session], int]:
        """
        Get sessions by status.

        Args:
            status: Session status to filter by
            agent_id: Optional agent filter
            page: Page number
            page_size: Items per page

        Returns:
            Tuple of (sessions list, total count)
        """
        query: dict[str, Any] = {"status": status.value}

        if agent_id:
            query["agent_id"] = agent_id

        total = self.collection.count_documents(query)
        skip = (page - 1) * page_size

        cursor = self.collection.find(query).sort(
            "last_active_at", -1
        ).skip(skip).limit(page_size)

        sessions = []
        for doc in cursor:
            doc.pop("_id", None)
            sessions.append(Session(**doc))

        return sessions, total

    def get_session_chain(self, session_id: str) -> List[Session]:
        """
        Get the full session chain for a session.

        Args:
            session_id: Session ID to get chain for

        Returns:
            List of sessions in the chain (oldest first)
        """
        session = self.get_by_id(session_id)
        if not session:
            return []

        # Get original session ID
        original_id = session.original_session_id or session_id

        # Find all sessions in the chain
        query = {
            "$or": [
                {"session_id": original_id},
                {"original_session_id": original_id},
                {"parent_session_id": original_id},
            ]
        }

        cursor = self.collection.find(query).sort("started_at", 1)

        sessions = []
        for doc in cursor:
            doc.pop("_id", None)
            sessions.append(Session(**doc))

        return sessions

    # =========================================================================
    # UPDATE OPERATIONS
    # =========================================================================

    def update_session(
        self,
        session_id: str,
        data: SessionUpdate,
    ) -> Optional[Session]:
        """
        Update session information.

        Args:
            session_id: Session identifier
            data: Update data

        Returns:
            Updated session or None if not found
        """
        update_doc: dict[str, Any] = {
            "last_active_at": datetime.utcnow()
        }

        if data.status is not None:
            update_doc["status"] = data.status.value
            if data.status == SessionStatus.PAUSED:
                update_doc["paused_at"] = datetime.utcnow()
            elif data.status == SessionStatus.RESUMED:
                update_doc["resumed_at"] = datetime.utcnow()
            elif data.status in [SessionStatus.COMPLETED, SessionStatus.ABANDONED]:
                update_doc["ended_at"] = datetime.utcnow()

        if data.task_description is not None:
            update_doc["metadata.task_description"] = data.task_description

        if data.current_step is not None:
            update_doc["metadata.current_step"] = data.current_step

        if data.working_files is not None:
            update_doc["metadata.working_files"] = data.working_files

        if data.loaded_contexts is not None:
            update_doc["metadata.loaded_contexts"] = data.loaded_contexts

        if data.todo_items is not None:
            update_doc["metadata.todo_items"] = data.todo_items

        if data.custom_data is not None:
            update_doc["metadata.custom_data"] = data.custom_data

        if data.knowledge_ids is not None:
            update_doc["knowledge_ids"] = data.knowledge_ids

        if data.timeout_minutes is not None:
            update_doc["timeout_minutes"] = data.timeout_minutes

        result = self.collection.find_one_and_update(
            {"session_id": session_id},
            {"$set": update_doc},
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.debug(f"Session {session_id} updated")
            return Session(**result)
        return None

    def update_activity(self, session_id: str) -> Optional[Session]:
        """
        Update session activity timestamp (heartbeat).

        Args:
            session_id: Session identifier

        Returns:
            Updated session or None if not found
        """
        result = self.collection.find_one_and_update(
            {"session_id": session_id},
            {"$set": {"last_active_at": datetime.utcnow()}},
            return_document=True
        )

        if result:
            result.pop("_id", None)
            return Session(**result)
        return None

    def pause_session(self, session_id: str) -> Optional[Session]:
        """
        Pause a session.

        Args:
            session_id: Session identifier

        Returns:
            Updated session or None if not found
        """
        now = datetime.utcnow()
        result = self.collection.find_one_and_update(
            {
                "session_id": session_id,
                "status": {"$in": [SessionStatus.ACTIVE.value, SessionStatus.RESUMED.value]}
            },
            {
                "$set": {
                    "status": SessionStatus.PAUSED.value,
                    "paused_at": now,
                    "last_active_at": now,
                },
                "$inc": {"pause_count": 1}
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(f"Session {session_id} paused")
            return Session(**result)
        return None

    def resume_session(self, session_id: str) -> Optional[Session]:
        """
        Resume a paused session.

        Args:
            session_id: Session identifier

        Returns:
            Updated session or None if not found
        """
        now = datetime.utcnow()
        result = self.collection.find_one_and_update(
            {
                "session_id": session_id,
                "status": SessionStatus.PAUSED.value
            },
            {
                "$set": {
                    "status": SessionStatus.RESUMED.value,
                    "resumed_at": now,
                    "last_active_at": now,
                }
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(f"Session {session_id} resumed")
            return Session(**result)
        return None

    def end_session(
        self,
        session_id: str,
        status: SessionStatus = SessionStatus.COMPLETED,
    ) -> Optional[Session]:
        """
        End a session.

        Args:
            session_id: Session identifier
            status: Final status (COMPLETED or ABANDONED)

        Returns:
            Updated session or None if not found
        """
        if status not in [SessionStatus.COMPLETED, SessionStatus.ABANDONED]:
            status = SessionStatus.COMPLETED

        now = datetime.utcnow()

        # Get session to calculate active time
        session = self.get_by_id(session_id)
        if not session:
            return None

        active_time = int((now - session.started_at).total_seconds())

        result = self.collection.find_one_and_update(
            {
                "session_id": session_id,
                "status": {"$nin": [SessionStatus.COMPLETED.value, SessionStatus.ABANDONED.value]}
            },
            {
                "$set": {
                    "status": status.value,
                    "ended_at": now,
                    "last_active_at": now,
                    "total_active_time_seconds": active_time,
                }
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(f"Session {session_id} ended with status {status.value}")
            return Session(**result)
        return None

    # =========================================================================
    # GIT CONTEXT OPERATIONS
    # =========================================================================

    def save_git_context(
        self,
        session_id: str,
        git_context: GitContextSnapshot,
    ) -> Optional[Session]:
        """
        Save git context snapshot to session.

        Args:
            session_id: Session identifier
            git_context: Git context snapshot

        Returns:
            Updated session or None if not found
        """
        result = self.collection.find_one_and_update(
            {"session_id": session_id},
            {
                "$set": {
                    "git_context": git_context.model_dump(),
                    "git_context_captured_at": datetime.utcnow(),
                    "last_active_at": datetime.utcnow(),
                }
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.debug(f"Git context saved for session {session_id}")
            return Session(**result)
        return None

    def save_patch_bundle(
        self,
        session_id: str,
        patch_bundle: PatchBundle,
    ) -> Optional[Session]:
        """
        Save patch bundle to session.

        Args:
            session_id: Session identifier
            patch_bundle: Patch bundle

        Returns:
            Updated session or None if not found
        """
        result = self.collection.find_one_and_update(
            {"session_id": session_id},
            {
                "$set": {
                    "patch_bundle": patch_bundle.model_dump(),
                    "last_active_at": datetime.utcnow(),
                }
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.debug(f"Patch bundle saved for session {session_id}")
            return Session(**result)
        return None

    # =========================================================================
    # HANDOFF OPERATIONS
    # =========================================================================

    def mark_handed_off(
        self,
        session_id: str,
        target_agent_id: str,
        target_machine_id: str,
        notes: Optional[str] = None,
    ) -> Optional[Session]:
        """
        Mark a session as handed off.

        Args:
            session_id: Session identifier
            target_agent_id: Target agent
            target_machine_id: Target machine
            notes: Handoff notes

        Returns:
            Updated session or None if not found
        """
        now = datetime.utcnow()

        # Get session to calculate active time
        session = self.get_by_id(session_id)
        if not session:
            return None

        active_time = int((now - session.started_at).total_seconds())

        result = self.collection.find_one_and_update(
            {"session_id": session_id},
            {
                "$set": {
                    "status": SessionStatus.HANDED_OFF.value,
                    "handed_off_to_agent": target_agent_id,
                    "handed_off_to_machine": target_machine_id,
                    "handoff_timestamp": now,
                    "handoff_notes": notes,
                    "ended_at": now,
                    "last_active_at": now,
                    "total_active_time_seconds": active_time,
                },
                "$inc": {"handoff_count": 1}
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            logger.info(
                f"Session {session_id} handed off to {target_agent_id} "
                f"on {target_machine_id}"
            )
            return Session(**result)
        return None

    def add_knowledge_id(
        self,
        session_id: str,
        knowledge_id: str,
    ) -> Optional[Session]:
        """
        Add a knowledge ID to session's generated knowledge list.

        Args:
            session_id: Session identifier
            knowledge_id: Knowledge entry ID

        Returns:
            Updated session or None if not found
        """
        result = self.collection.find_one_and_update(
            {"session_id": session_id},
            {
                "$addToSet": {"knowledge_ids": knowledge_id},
                "$set": {"last_active_at": datetime.utcnow()}
            },
            return_document=True
        )

        if result:
            result.pop("_id", None)
            return Session(**result)
        return None

    # =========================================================================
    # CLEANUP & STATISTICS
    # =========================================================================

    def find_expired_sessions(
        self,
        include_paused: bool = False,
    ) -> List[Session]:
        """
        Find sessions that have timed out.

        Args:
            include_paused: Whether to include paused sessions

        Returns:
            List of expired sessions
        """
        # Build query for active/resumed sessions
        status_filter = [SessionStatus.ACTIVE.value, SessionStatus.RESUMED.value]
        if include_paused:
            status_filter.append(SessionStatus.PAUSED.value)

        # Find sessions and check timeout individually
        # (since timeout_minutes varies per session)
        cursor = self.collection.find({
            "status": {"$in": status_filter}
        })

        expired = []
        now = datetime.utcnow()

        for doc in cursor:
            doc.pop("_id", None)
            session = Session(**doc)
            timeout_delta = timedelta(minutes=session.timeout_minutes)
            if now > (session.last_active_at + timeout_delta):
                expired.append(session)

        return expired

    def mark_expired_sessions(self) -> int:
        """
        Find and mark expired sessions as abandoned.

        Returns:
            Number of sessions marked as abandoned
        """
        expired = self.find_expired_sessions()

        count = 0
        for session in expired:
            result = self.end_session(session.session_id, SessionStatus.ABANDONED)
            if result:
                count += 1

        if count > 0:
            logger.info(f"Marked {count} sessions as abandoned due to timeout")

        return count

    def get_stats(
        self,
        agent_id: Optional[str] = None,
    ) -> SessionStats:
        """
        Get session statistics.

        Args:
            agent_id: Optional agent filter

        Returns:
            Session statistics
        """
        base_query: dict[str, Any] = {}
        if agent_id:
            base_query["agent_id"] = agent_id

        # Count by status
        status_counts = {}
        for status in SessionStatus:
            query = {**base_query, "status": status.value}
            status_counts[status.value] = self.collection.count_documents(query)

        # Count by agent
        agent_pipeline = [
            {"$match": base_query} if base_query else {"$match": {}},
            {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}}
        ]
        by_agent = {}
        for doc in self.collection.aggregate(agent_pipeline):
            if doc["_id"]:
                by_agent[doc["_id"]] = doc["count"]

        # Count by machine
        machine_pipeline = [
            {"$match": base_query} if base_query else {"$match": {}},
            {"$group": {"_id": "$machine_id", "count": {"$sum": 1}}}
        ]
        by_machine = {}
        for doc in self.collection.aggregate(machine_pipeline):
            if doc["_id"]:
                by_machine[doc["_id"]] = doc["count"]

        # Count handoffs
        handoff_query = {**base_query, "handoff_count": {"$gt": 0}}
        total_handoffs = self.collection.count_documents(handoff_query)

        return SessionStats(
            total_sessions=sum(status_counts.values()),
            active_sessions=status_counts.get(SessionStatus.ACTIVE.value, 0) +
                           status_counts.get(SessionStatus.RESUMED.value, 0),
            paused_sessions=status_counts.get(SessionStatus.PAUSED.value, 0),
            completed_sessions=status_counts.get(SessionStatus.COMPLETED.value, 0),
            abandoned_sessions=status_counts.get(SessionStatus.ABANDONED.value, 0),
            total_handoffs=total_handoffs,
            by_agent=by_agent,
            by_machine=by_machine,
        )

    def cleanup_old_sessions(
        self,
        older_than_days: int = 30,
    ) -> int:
        """
        Delete old completed/abandoned sessions.

        Args:
            older_than_days: Delete sessions older than this

        Returns:
            Number of sessions deleted
        """
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)

        result = self.collection.delete_many({
            "status": {"$in": [
                SessionStatus.COMPLETED.value,
                SessionStatus.ABANDONED.value,
                SessionStatus.HANDED_OFF.value,
            ]},
            "ended_at": {"$lt": cutoff}
        })

        if result.deleted_count > 0:
            logger.info(f"Cleaned up {result.deleted_count} old sessions")

        return result.deleted_count


class HandoffRepository:
    """
    Repository for session handoff operations.

    Handles handoff requests, packages, and tracking.
    """

    def __init__(self, collection: Collection):
        """
        Initialize handoff repository.

        Args:
            collection: MongoDB collection for dakb_sessions (handoffs subdocument)
        """
        self.collection = collection

    def create_handoff_request(
        self,
        request: HandoffRequest,
    ) -> HandoffRequest:
        """
        Create a handoff request.

        Args:
            request: Handoff request data

        Returns:
            Created handoff request
        """
        if not request.handoff_id:
            request.handoff_id = generate_handoff_id()

        # Store in session document
        self.collection.update_one(
            {"session_id": request.source_session_id},
            {
                "$set": {
                    "pending_handoff": request.model_dump()
                }
            }
        )

        logger.info(
            f"Handoff request {request.handoff_id} created for session "
            f"{request.source_session_id}"
        )

        return request

    def get_handoff_request(
        self,
        handoff_id: str,
    ) -> Optional[HandoffRequest]:
        """
        Get a handoff request by ID.

        Args:
            handoff_id: Handoff identifier

        Returns:
            Handoff request or None if not found
        """
        doc = self.collection.find_one(
            {"pending_handoff.handoff_id": handoff_id},
            {"pending_handoff": 1}
        )

        if doc and "pending_handoff" in doc:
            return HandoffRequest(**doc["pending_handoff"])
        return None

    def update_handoff_status(
        self,
        handoff_id: str,
        status: HandoffStatus,
        result_session_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[HandoffRequest]:
        """
        Update handoff request status.

        Args:
            handoff_id: Handoff identifier
            status: New status
            result_session_id: New session ID if successful
            error_message: Error message if failed

        Returns:
            Updated handoff request or None
        """
        update_doc: dict[str, Any] = {
            "pending_handoff.status": status.value
        }

        if status == HandoffStatus.ACCEPTED:
            update_doc["pending_handoff.accepted_at"] = datetime.utcnow()
        elif status == HandoffStatus.APPLIED:
            update_doc["pending_handoff.applied_at"] = datetime.utcnow()
            if result_session_id:
                update_doc["pending_handoff.result_session_id"] = result_session_id

        if error_message:
            update_doc["pending_handoff.error_message"] = error_message

        result = self.collection.find_one_and_update(
            {"pending_handoff.handoff_id": handoff_id},
            {"$set": update_doc},
            return_document=True
        )

        if result and "pending_handoff" in result:
            return HandoffRequest(**result["pending_handoff"])
        return None

    def get_pending_handoffs(
        self,
        target_agent_id: Optional[str] = None,
        target_machine_id: Optional[str] = None,
    ) -> List[HandoffRequest]:
        """
        Get pending handoff requests for a target.

        Args:
            target_agent_id: Target agent filter
            target_machine_id: Target machine filter

        Returns:
            List of pending handoff requests
        """
        query: dict[str, Any] = {
            "pending_handoff.status": HandoffStatus.PENDING.value,
            "pending_handoff.expires_at": {"$gt": datetime.utcnow()}
        }

        if target_agent_id:
            query["$or"] = [
                {"pending_handoff.target_agent_id": target_agent_id},
                {"pending_handoff.target_agent_id": None}  # Any agent
            ]

        if target_machine_id:
            if "$or" in query:
                # Combine with existing $or
                query["$and"] = [
                    {"$or": query.pop("$or")},
                    {"$or": [
                        {"pending_handoff.target_machine_id": target_machine_id},
                        {"pending_handoff.target_machine_id": None}
                    ]}
                ]
            else:
                query["$or"] = [
                    {"pending_handoff.target_machine_id": target_machine_id},
                    {"pending_handoff.target_machine_id": None}
                ]

        cursor = self.collection.find(query, {"pending_handoff": 1})

        requests = []
        for doc in cursor:
            if "pending_handoff" in doc:
                requests.append(HandoffRequest(**doc["pending_handoff"]))

        return requests

    def cleanup_expired_handoffs(self) -> int:
        """
        Clean up expired handoff requests.

        Returns:
            Number of handoffs cleaned up
        """
        result = self.collection.update_many(
            {
                "pending_handoff.status": HandoffStatus.PENDING.value,
                "pending_handoff.expires_at": {"$lt": datetime.utcnow()}
            },
            {
                "$set": {
                    "pending_handoff.status": HandoffStatus.CANCELLED.value
                }
            }
        )

        if result.modified_count > 0:
            logger.info(f"Cleaned up {result.modified_count} expired handoff requests")

        return result.modified_count
