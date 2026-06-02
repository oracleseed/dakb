"""
DAKB Task Timeout Monitor

Background task that periodically checks for:
1. Zombie tasks -- lease expired but task still processing
2. Timed-out tasks -- past expires_at deadline

Version: 1.0
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class TaskTimeoutMonitor:
    """
    Background monitor for task leases and timeouts.

    Runs periodically to:
    - Detect zombie tasks (lease expired in Redis but MongoDB status=processing)
    - Detect timed-out tasks (past expires_at)
    - Release zombies back to pending for re-assignment
    - Mark timed-out tasks as timeout
    """

    def __init__(
        self,
        task_router,
        redis_client,
        conn_manager,
        mongo_collection,
        check_interval: float = 10.0,
    ):
        self._task_router = task_router
        self._redis = redis_client
        self._conn = conn_manager
        self._mongo = mongo_collection
        self._interval = check_interval
        self._running = False
        self._stats = {
            "zombies_released": 0,
            "tasks_timed_out": 0,
            "check_count": 0,
        }

    async def start(self) -> None:
        """Start the monitor loop."""
        self._running = True
        logger.info("Task monitor started (interval=%ss)", self._interval)
        while self._running:
            try:
                await self.check_zombies()
                await self.check_timeouts()
                self._stats["check_count"] += 1
            except Exception as e:
                logger.error("Task monitor error: %s", e)
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        """Stop the monitor loop."""
        self._running = False
        logger.info("Task monitor stopped")

    async def check_zombies(self) -> list[str]:
        """
        Detect and release zombie tasks.

        A zombie is a task in claimed/processing status whose Redis lease
        has expired (agent crashed or disconnected).
        """
        from dakb.db.schemas import DelegatedTaskStatus

        # Get all active leases from Redis
        active_leases = await self._redis.get_active_leases()

        # Get all claimed/processing tasks from MongoDB
        active_statuses = [
            DelegatedTaskStatus.CLAIMED.value,
            DelegatedTaskStatus.PROCESSING.value,
        ]
        active_tasks = list(self._mongo.find({"status": {"$in": active_statuses}}))

        released = []
        for task_doc in active_tasks:
            task_id = task_doc["task_id"]
            if task_id not in active_leases:
                # Lease expired -- this is a zombie
                try:
                    await self._task_router.release_zombie(task_id)
                    released.append(task_id)
                    self._stats["zombies_released"] += 1

                    # Notify via WebSocket
                    from dakb.gateway.agent_websocket import make_notification
                    notification = make_notification("task.released", {
                        "task_id": task_id,
                        "reason": "lease_expired",
                        "previous_assignee": task_doc.get("assigned_to"),
                    })
                    # Notify requester
                    requester = task_doc.get("requester_id")
                    if requester:
                        await self._conn.send_to_agent(requester, notification)

                    logger.warning(
                        "Zombie released: %s (was assigned to %s)",
                        task_id, task_doc.get("assigned_to"),
                    )
                except Exception as e:
                    logger.error("Failed to release zombie %s: %s", task_id, e)

        return released

    async def check_timeouts(self) -> list[str]:
        """
        Detect and mark timed-out tasks.

        A task times out when current time > expires_at.
        """
        from dakb.db.schemas import DelegatedTaskStatus

        now = datetime.now(timezone.utc)

        # Query tasks past their expiry that haven't been completed
        active_statuses = [
            DelegatedTaskStatus.PENDING.value,
            DelegatedTaskStatus.CLAIMED.value,
            DelegatedTaskStatus.PROCESSING.value,
        ]
        expired_tasks = list(self._mongo.find({
            "status": {"$in": active_statuses},
            "expires_at": {"$lt": now},
        }))

        timed_out = []
        for task_doc in expired_tasks:
            task_id = task_doc["task_id"]
            try:
                await self._task_router.timeout_task(task_id)
                timed_out.append(task_id)
                self._stats["tasks_timed_out"] += 1

                # Notify requester and assignee
                from dakb.gateway.agent_websocket import make_notification
                notification = make_notification("task.timeout", {
                    "task_id": task_id,
                    "requester_id": task_doc.get("requester_id"),
                    "assigned_to": task_doc.get("assigned_to"),
                })
                for agent in [
                    task_doc.get("requester_id"),
                    task_doc.get("assigned_to"),
                ]:
                    if agent:
                        await self._conn.send_to_agent(agent, notification)

                logger.warning("Task timeout: %s", task_id)
            except Exception as e:
                logger.error("Failed to timeout task %s: %s", task_id, e)

        return timed_out

    def get_stats(self) -> dict:
        """Get monitoring statistics."""
        return dict(self._stats)
