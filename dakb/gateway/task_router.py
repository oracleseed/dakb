"""
DAKB Task Router

Core task delegation engine with lifecycle management,
direct assignment, capability routing, and lease-based zombie prevention.

Persists to the ``dakb_tasks`` MongoDB collection and publishes lifecycle
events to the ``stream:tasks:updates`` Redis stream.

Version: 1.0
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    # Schemas are imported lazily inside methods at runtime so this module
    # imports cleanly even before dakb.db.schemas defines the task models.
    # This guard exists only for static type-checkers / linters.
    from dakb.db.schemas import DakbTask

logger = logging.getLogger(__name__)


class TaskRouter:
    """
    Routes and manages task delegation between agents.

    Supports:
    - Direct assignment (recipient_id specified)
    - Capability routing (find best agent by capabilities + status + load)
    - Lease-based zombie prevention (Redis TTL keys)
    - Full lifecycle: pending -> claimed -> processing -> completed/failed/timeout
    """

    def __init__(
        self, redis_client, presence_manager, conn_manager, mongo_collection
    ):
        self._redis = redis_client
        self._presence = presence_manager
        self._conn = conn_manager
        self._mongo = mongo_collection

    async def create_task(self, create) -> "DakbTask":
        """
        Create a new delegated task.

        Generates task_id, persists to MongoDB, publishes to
        stream:tasks:updates.
        """
        from dakb.db.schemas import DakbTask, DelegatedTaskStatus

        now = datetime.now(timezone.utc)
        task_id = f"task_{now.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

        task = DakbTask(
            task_id=task_id,
            task_type=create.task_type,
            requester_id=create.requester_id,
            recipient_id=create.recipient_id,
            required_capabilities=create.required_capabilities,
            prefer_status=create.prefer_status,
            payload=create.payload,
            status=DelegatedTaskStatus.PENDING,
            timeout_seconds=create.timeout_seconds,
            lease_ttl_seconds=create.lease_ttl_seconds,
            created_at=now,
            expires_at=now + timedelta(seconds=create.timeout_seconds),
        )

        # Persist to MongoDB
        self._mongo.insert_one(task.model_dump())

        # Publish to stream:tasks:updates
        await self._redis.stream_add("stream:tasks:updates", {
            "data": json.dumps({
                "event": "task.created",
                "task_id": task_id,
                "task_type": create.task_type,
                "requester_id": create.requester_id,
                "recipient_id": create.recipient_id,
            })
        })

        logger.info(
            "Task created: %s type=%s requester=%s recipient=%s",
            task_id, create.task_type, create.requester_id,
            create.recipient_id,
        )
        return task

    async def claim_task(self, task_id: str, agent_name: str) -> "DakbTask":
        """
        Claim a pending task. Sets status=claimed, creates lease.

        Raises ValueError if task not found or not pending.
        """
        from dakb.db.schemas import DakbTask, DelegatedTaskStatus

        doc = self._mongo.find_one({"task_id": task_id})
        if not doc:
            raise ValueError(f"Task '{task_id}' not found")

        task = DakbTask(**doc)
        if task.status != DelegatedTaskStatus.PENDING:
            raise ValueError(
                f"Task '{task_id}' is not pending (status={task.status.value})"
            )

        now = datetime.now(timezone.utc)
        update = {
            "status": DelegatedTaskStatus.CLAIMED.value,
            "assigned_to": agent_name,
            "claimed_at": now,
            "last_heartbeat": now,
        }

        self._mongo.update_one({"task_id": task_id}, {"$set": update})

        # Set Redis lease
        await self._redis.set_lease(
            task_id, agent_name, ttl_seconds=task.lease_ttl_seconds
        )

        # Publish claim event
        await self._redis.stream_add("stream:tasks:updates", {
            "data": json.dumps({
                "event": "task.claimed",
                "task_id": task_id,
                "assigned_to": agent_name,
            })
        })

        task.status = DelegatedTaskStatus.CLAIMED
        task.assigned_to = agent_name
        task.claimed_at = now
        task.last_heartbeat = now

        logger.info("Task claimed: %s -> %s", task_id, agent_name)
        return task

    async def start_processing(self, task_id: str) -> "DakbTask":
        """Transition task from claimed to processing."""
        from dakb.db.schemas import DakbTask, DelegatedTaskStatus

        doc = self._mongo.find_one({"task_id": task_id})
        if not doc:
            raise ValueError(f"Task '{task_id}' not found")

        task = DakbTask(**doc)
        if task.status != DelegatedTaskStatus.CLAIMED:
            raise ValueError(
                f"Task '{task_id}' is not claimed (status={task.status.value})"
            )

        self._mongo.update_one(
            {"task_id": task_id},
            {"$set": {"status": DelegatedTaskStatus.PROCESSING.value}},
        )

        task.status = DelegatedTaskStatus.PROCESSING
        logger.info("Task processing: %s", task_id)
        return task

    async def heartbeat(self, task_id: str) -> bool:
        """
        Renew task lease. Returns False if lease expired (zombie risk).

        Agent must call this every lease_ttl_seconds to keep task alive.
        """
        doc = self._mongo.find_one({"task_id": task_id})
        if not doc:
            return False

        renewed = await self._redis.renew_lease(
            task_id, ttl_seconds=doc.get("lease_ttl_seconds", 60)
        )

        if renewed:
            now = datetime.now(timezone.utc)
            self._mongo.update_one(
                {"task_id": task_id},
                {"$set": {"last_heartbeat": now}},
            )

        return renewed

    async def complete_task(self, task_id: str, result: dict) -> "DakbTask":
        """Mark task as completed with result. Deletes lease."""
        from dakb.db.schemas import DakbTask, DelegatedTaskStatus

        doc = self._mongo.find_one({"task_id": task_id})
        if not doc:
            raise ValueError(f"Task '{task_id}' not found")

        now = datetime.now(timezone.utc)
        update = {
            "status": DelegatedTaskStatus.COMPLETED.value,
            "result": result,
            "completed_at": now,
        }

        self._mongo.update_one({"task_id": task_id}, {"$set": update})
        await self._redis.delete_lease(task_id)

        # Publish completion event
        await self._redis.stream_add("stream:tasks:updates", {
            "data": json.dumps({
                "event": "task.completed",
                "task_id": task_id,
                "requester_id": doc.get("requester_id"),
            })
        })

        task = DakbTask(**doc)
        task.status = DelegatedTaskStatus.COMPLETED
        task.result = result
        task.completed_at = now

        logger.info("Task completed: %s", task_id)
        return task

    async def fail_task(self, task_id: str, error: str) -> "DakbTask":
        """Mark task as failed with error message. Deletes lease."""
        from dakb.db.schemas import DakbTask, DelegatedTaskStatus

        doc = self._mongo.find_one({"task_id": task_id})
        if not doc:
            raise ValueError(f"Task '{task_id}' not found")

        now = datetime.now(timezone.utc)
        update = {
            "status": DelegatedTaskStatus.FAILED.value,
            "error": error,
            "completed_at": now,
        }

        self._mongo.update_one({"task_id": task_id}, {"$set": update})
        await self._redis.delete_lease(task_id)

        # Publish failure event
        await self._redis.stream_add("stream:tasks:updates", {
            "data": json.dumps({
                "event": "task.failed",
                "task_id": task_id,
                "requester_id": doc.get("requester_id"),
                "error": error,
            })
        })

        task = DakbTask(**doc)
        task.status = DelegatedTaskStatus.FAILED
        task.error = error
        task.completed_at = now

        logger.info("Task failed: %s -- %s", task_id, error)
        return task

    async def release_zombie(self, task_id: str) -> "DakbTask":
        """
        Release a zombie task back to pending. Called when lease expires.
        """
        from dakb.db.schemas import DakbTask, DelegatedTaskStatus

        doc = self._mongo.find_one({"task_id": task_id})
        if not doc:
            raise ValueError(f"Task '{task_id}' not found")

        previous_assignee = doc.get("assigned_to")
        update = {
            "status": DelegatedTaskStatus.PENDING.value,
            "assigned_to": None,
            "claimed_at": None,
            "last_heartbeat": None,
        }

        self._mongo.update_one({"task_id": task_id}, {"$set": update})

        # Publish release event
        await self._redis.stream_add("stream:tasks:updates", {
            "data": json.dumps({
                "event": "task.released",
                "task_id": task_id,
                "reason": "lease_expired",
                "previous_assignee": previous_assignee,
            })
        })

        task = DakbTask(**doc)
        task.status = DelegatedTaskStatus.PENDING
        task.assigned_to = None
        task.claimed_at = None
        task.last_heartbeat = None

        logger.info("Zombie released: %s (was %s)", task_id, previous_assignee)
        return task

    async def timeout_task(self, task_id: str) -> "DakbTask":
        """Mark task as timed out. Called when expires_at is past."""
        from dakb.db.schemas import DakbTask, DelegatedTaskStatus

        doc = self._mongo.find_one({"task_id": task_id})
        if not doc:
            raise ValueError(f"Task '{task_id}' not found")

        now = datetime.now(timezone.utc)
        update = {
            "status": DelegatedTaskStatus.TIMEOUT.value,
            "completed_at": now,
        }

        self._mongo.update_one({"task_id": task_id}, {"$set": update})
        await self._redis.delete_lease(task_id)

        # Publish timeout event
        await self._redis.stream_add("stream:tasks:updates", {
            "data": json.dumps({
                "event": "task.timeout",
                "task_id": task_id,
                "requester_id": doc.get("requester_id"),
                "assigned_to": doc.get("assigned_to"),
            })
        })

        task = DakbTask(**doc)
        task.status = DelegatedTaskStatus.TIMEOUT
        task.completed_at = now

        logger.info("Task timeout: %s", task_id)
        return task

    async def get_task(self, task_id: str) -> Optional["DakbTask"]:
        """Retrieve a task from MongoDB by task_id."""
        from dakb.db.schemas import DakbTask

        doc = self._mongo.find_one({"task_id": task_id})
        if not doc:
            return None
        return DakbTask(**doc)

    async def list_tasks(
        self,
        status: str | None = None,
        assigned_to: str | None = None,
        requester_id: str | None = None,
        limit: int = 50,
    ) -> list["DakbTask"]:
        """Query tasks with optional filters."""
        from dakb.db.schemas import DakbTask

        query: dict = {}
        if status:
            query["status"] = status
        if assigned_to:
            query["assigned_to"] = assigned_to
        if requester_id:
            query["requester_id"] = requester_id

        cursor = self._mongo.find(query).sort("created_at", -1).limit(limit)
        return [DakbTask(**doc) for doc in cursor]

    async def find_best_agent(
        self,
        required_capabilities: list[str],
        prefer_status: list[str] | None = None,
    ) -> str | None:
        """
        Find the best available agent matching required capabilities.

        Algorithm:
        1. Query PresenceManager for online/idle agents
        2. Filter by required capabilities (ALL must match)
        3. Sort by: preferred status match first, then lowest load
        4. Return best match or None
        """
        filter_status = prefer_status or ["online", "idle"]
        all_agents = await self._presence.query(
            filter_status=filter_status,
            filter_capabilities=required_capabilities,
        )

        if not all_agents:
            logger.info(
                "No agent found for capabilities=%s status=%s",
                required_capabilities, filter_status,
            )
            return None

        # Score and sort agents
        candidates = []
        for name, data in all_agents.items():
            # Verify ALL required capabilities are present
            agent_caps = set(data.get("capabilities", []))
            if not all(cap in agent_caps for cap in required_capabilities):
                continue

            load = data.get("metadata", {}).get("load", 0.5)
            status = data.get("status", "unknown")
            # Prefer agents with preferred status
            status_score = 0 if status in (prefer_status or ["online"]) else 1
            candidates.append((name, status_score, load))

        if not candidates:
            logger.info(
                "No agent matched all capabilities=%s", required_capabilities
            )
            return None

        # Sort by: status_score ascending (preferred first), load ascending
        candidates.sort(key=lambda c: (c[1], c[2]))

        best = candidates[0][0]
        logger.info(
            "Best agent for capabilities=%s: %s (from %d candidates)",
            required_capabilities, best, len(candidates),
        )
        return best
