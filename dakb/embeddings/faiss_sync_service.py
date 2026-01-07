"""
DAKB FAISS/MongoDB Synchronization Service

Ensures FAISS index stays synchronized with MongoDB knowledge collection.
Implements 3-layer protection strategy for handling deletions and TTL expiry.

Version: 1.0
Created: 2025-12-07
Author: Backend Agent (Claude Opus 4.5)

3-Layer Protection Strategy:
  Layer 1: Immediate soft-delete (on delete/deprecate API call)
  Layer 2: Periodic sync job (hourly background task)
  Layer 3: Search-time filtering (skip None entries in results)

v1.3 Compliance:
  - Uses embedding_service.get_deletion_count() as SINGLE SOURCE OF TRUTH
  - No local _deletion_count variable maintained
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from pymongo import MongoClient
from pymongo.database import Database

from .embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class FAISSMongoSyncService:
    """
    Ensures FAISS and MongoDB stay synchronized.
    Handles TTL expiry, deletions, and periodic index rebuilds.

    v1.3 FIX: Uses single source of truth for deletion count
    from embedding_service.get_deletion_count() to prevent divergence.

    Attributes:
        REBUILD_THRESHOLD: Fraction of deleted vectors that triggers rebuild (10%)
        MIN_VECTORS_FOR_REBUILD: Minimum vectors before considering rebuild (100)
        DEFAULT_SYNC_INTERVAL: Default interval between sync runs in seconds (3600 = 1 hour)
    """

    # Rebuild index when deletion ratio exceeds this threshold
    REBUILD_THRESHOLD = 0.10  # 10%

    # Minimum vectors before considering rebuild
    MIN_VECTORS_FOR_REBUILD = 100

    # Default sync interval (1 hour)
    DEFAULT_SYNC_INTERVAL = 3600

    def __init__(
        self,
        mongo_client: MongoClient,
        embedding_service: EmbeddingService,
        db_name: str = "dakb",
    ):
        """
        Initialize the FAISS/MongoDB sync service.

        Args:
            mongo_client: MongoDB client instance
            embedding_service: EmbeddingService instance with FAISS index
            db_name: Database name (default: dakb)
        """
        self.db: Database = mongo_client[db_name]
        self.embedding_service = embedding_service
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="faiss_sync")
        self._sync_task: asyncio.Task | None = None
        self._running = False

        # Sync statistics
        self._last_sync_at: datetime | None = None
        self._last_rebuild_at: datetime | None = None
        self._total_syncs = 0
        self._total_rebuilds = 0

        logger.info("FAISSMongoSyncService initialized")

    async def sync_deletions(self) -> dict:
        """
        Main sync job - run hourly via scheduler.
        Finds expired/deleted docs in MongoDB and removes from FAISS.

        This implements Layer 2 of the 3-layer protection strategy.

        Returns:
            Dictionary with sync statistics including:
            - synced_at: ISO timestamp of sync
            - expired_count: Number of expired documents found
            - deprecated_count: Number of deprecated documents found
            - deleted_count: Number of deleted documents found
            - removed_from_faiss: Number of vectors marked as deleted in FAISS
            - rebuild_triggered: Whether a rebuild was triggered
        """
        now = datetime.utcnow()
        ids_to_remove: set[str] = set()
        expired_count = 0
        deprecated_count = 0
        deleted_count = 0

        try:
            # Run MongoDB queries in thread pool to avoid blocking
            loop = asyncio.get_running_loop()

            # 1. Find documents that have expired (TTL)
            expired_ids = await loop.run_in_executor(
                self._executor,
                self._find_expired_documents,
                now
            )
            ids_to_remove.update(expired_ids)
            expired_count = len(expired_ids)

            # 2. Find documents marked as deprecated
            deprecated_ids = await loop.run_in_executor(
                self._executor,
                self._find_deprecated_documents
            )
            ids_to_remove.update(deprecated_ids)
            deprecated_count = len(deprecated_ids)

            # 3. Find documents that were soft-deleted
            deleted_ids = await loop.run_in_executor(
                self._executor,
                self._find_deleted_documents
            )
            ids_to_remove.update(deleted_ids)
            deleted_count = len(deleted_ids)

            # 4. Remove from FAISS (soft delete - mark as None)
            removed_count = 0
            for kid in ids_to_remove:
                if self.embedding_service.mark_deleted(kid):
                    removed_count += 1
                    # v1.3: No local counter - embedding_service tracks deletions internally

            logger.info(
                f"Sync complete: marked {removed_count} vectors as deleted in FAISS "
                f"(expired={expired_count}, deprecated={deprecated_count}, deleted={deleted_count})"
            )

            # 5. Check if rebuild is needed
            rebuild_triggered = await self.check_and_rebuild_if_needed()

            # Update statistics
            self._last_sync_at = now
            self._total_syncs += 1

            return {
                "synced_at": now.isoformat(),
                "expired_count": expired_count,
                "deprecated_count": deprecated_count,
                "deleted_count": deleted_count,
                "total_to_remove": len(ids_to_remove),
                "removed_from_faiss": removed_count,
                "rebuild_triggered": rebuild_triggered,
            }

        except Exception as e:
            logger.error(f"Sync error: {e}", exc_info=True)
            return {
                "synced_at": now.isoformat(),
                "error": str(e),
                "expired_count": expired_count,
                "deprecated_count": deprecated_count,
                "deleted_count": deleted_count,
                "removed_from_faiss": 0,
                "rebuild_triggered": False,
            }

    def _find_expired_documents(self, before: datetime) -> list[str]:
        """
        Find knowledge IDs with expired TTL.

        Args:
            before: Find documents expiring before this time

        Returns:
            List of expired knowledge IDs
        """
        cursor = self.db.dakb_knowledge.find(
            {
                "expires_at": {"$lt": before, "$ne": None}
            },
            {"knowledge_id": 1}
        )
        return [doc["knowledge_id"] for doc in cursor]

    def _find_deprecated_documents(self) -> list[str]:
        """
        Find knowledge IDs marked as deprecated.

        Returns:
            List of deprecated knowledge IDs
        """
        cursor = self.db.dakb_knowledge.find(
            {"status": "deprecated"},
            {"knowledge_id": 1}
        )
        return [doc["knowledge_id"] for doc in cursor]

    def _find_deleted_documents(self) -> list[str]:
        """
        Find knowledge IDs marked as deleted.

        Returns:
            List of deleted knowledge IDs
        """
        cursor = self.db.dakb_knowledge.find(
            {"status": "deleted"},
            {"knowledge_id": 1}
        )
        return [doc["knowledge_id"] for doc in cursor]

    async def check_and_rebuild_if_needed(self) -> bool:
        """
        Check if FAISS index needs rebuilding due to high deletion ratio.
        Triggers rebuild if deletion ratio exceeds threshold (10%).

        Uses embedding_service.get_deletion_count() as single source of truth
        for deletion count (v1.3 fix).

        Returns:
            True if rebuild was triggered, False otherwise
        """
        if self.embedding_service.index is None:
            logger.warning("No FAISS index initialized, skipping rebuild check")
            return False

        total_vectors = self.embedding_service.index.ntotal

        if total_vectors < self.MIN_VECTORS_FOR_REBUILD:
            logger.debug(
                f"Skipping rebuild check: {total_vectors} vectors "
                f"< minimum {self.MIN_VECTORS_FOR_REBUILD}"
            )
            return False

        # v1.3 FIX: Use single source of truth from embedding_service
        deletion_count = self.embedding_service.get_deletion_count()
        deletion_ratio = deletion_count / total_vectors

        logger.debug(
            f"Rebuild check: {deletion_count}/{total_vectors} deleted "
            f"({deletion_ratio:.2%} vs threshold {self.REBUILD_THRESHOLD:.2%})"
        )

        if deletion_ratio > self.REBUILD_THRESHOLD:
            logger.warning(
                f"Deletion ratio {deletion_ratio:.2%} exceeds threshold "
                f"{self.REBUILD_THRESHOLD:.2%}, triggering rebuild"
            )
            await self.full_rebuild()
            return True

        return False

    async def full_rebuild(self) -> dict:
        """
        Rebuild FAISS index from MongoDB ground truth.
        Run during low-traffic periods or when deletion ratio is high.

        Creates a backup before rebuilding and restores all active
        knowledge from MongoDB.

        Returns:
            Dictionary with rebuild statistics including:
            - rebuilt_at: ISO timestamp of rebuild
            - vector_count: Number of vectors in rebuilt index
            - elapsed_seconds: Time taken for rebuild
            - backup_path: Path to backup directory
        """
        logger.info("Starting FAISS index rebuild from MongoDB...")
        start_time = datetime.utcnow()

        try:
            # 1. Backup current index
            backup_path = self.embedding_service.backup_index()
            logger.info(f"Index backed up to: {backup_path}")

            # 2. Get all ACTIVE knowledge from MongoDB (run in executor)
            loop = asyncio.get_running_loop()
            documents = await loop.run_in_executor(
                self._executor,
                self._get_active_documents_for_rebuild
            )

            logger.info(f"Found {len(documents)} active documents for rebuild")

            # 3. Rebuild index (thread-safe, handled by embedding_service)
            self.embedding_service.rebuild_from_documents(documents)

            # 4. Update statistics
            self._last_rebuild_at = datetime.utcnow()
            self._total_rebuilds += 1

            elapsed = (datetime.utcnow() - start_time).total_seconds()
            logger.info(f"FAISS index rebuilt in {elapsed:.2f}s with {len(documents)} vectors")

            return {
                "rebuilt_at": datetime.utcnow().isoformat(),
                "vector_count": len(documents),
                "elapsed_seconds": elapsed,
                "backup_path": backup_path,
            }

        except Exception as e:
            logger.error(f"Rebuild failed: {e}", exc_info=True)
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            return {
                "rebuilt_at": datetime.utcnow().isoformat(),
                "error": str(e),
                "elapsed_seconds": elapsed,
            }

    def _get_active_documents_for_rebuild(self) -> list[dict]:
        """
        Get all active knowledge documents for FAISS rebuild.

        Returns:
            List of documents with knowledge_id, title, content keys
        """
        now = datetime.utcnow()
        cursor = self.db.dakb_knowledge.find(
            {
                "embedding_indexed": True,
                "status": {"$nin": ["deprecated", "deleted"]},
                "$or": [
                    {"expires_at": None},
                    {"expires_at": {"$gt": now}}
                ]
            },
            {"knowledge_id": 1, "title": 1, "content": 1}
        )

        documents = []
        for doc in cursor:
            # Remove MongoDB _id field
            doc.pop("_id", None)
            documents.append(doc)

        return documents

    async def run_periodic_sync(self, interval_seconds: int = DEFAULT_SYNC_INTERVAL):
        """
        Background task for periodic synchronization.
        Runs sync_deletions() at the specified interval.

        Args:
            interval_seconds: Time between sync runs in seconds (default: 3600 = 1 hour)

        Note:
            This method runs indefinitely until stop_periodic_sync() is called
            or the task is cancelled.
        """
        self._running = True
        logger.info(f"Starting periodic sync (interval: {interval_seconds}s)")

        while self._running:
            try:
                # Run sync
                result = await self.sync_deletions()
                logger.info(
                    f"Periodic sync complete: removed {result.get('removed_from_faiss', 0)} vectors"
                )

                # Wait for next interval
                await asyncio.sleep(interval_seconds)

            except asyncio.CancelledError:
                logger.info("Periodic sync cancelled")
                break
            except Exception as e:
                logger.error(f"Periodic sync error: {e}", exc_info=True)
                # Wait before retry to avoid rapid failure loop
                await asyncio.sleep(60)

        self._running = False
        logger.info("Periodic sync stopped")

    def start_periodic_sync(
        self,
        interval_seconds: int = DEFAULT_SYNC_INTERVAL
    ) -> asyncio.Task:
        """
        Start the periodic sync background task.

        Args:
            interval_seconds: Time between sync runs in seconds

        Returns:
            The created asyncio.Task

        Raises:
            RuntimeError: If periodic sync is already running
        """
        if self._sync_task is not None and not self._sync_task.done():
            raise RuntimeError("Periodic sync is already running")

        self._sync_task = asyncio.create_task(
            self.run_periodic_sync(interval_seconds),
            name="faiss_mongo_sync"
        )
        return self._sync_task

    def stop_periodic_sync(self):
        """
        Stop the periodic sync background task.
        Gracefully cancels the running task if active.
        """
        self._running = False
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
            logger.info("Periodic sync stop requested")

    async def verify_consistency(self) -> dict:
        """
        Verify FAISS and MongoDB are in sync.
        Use as a health check to detect synchronization issues.

        Returns:
            Dictionary with consistency check results:
            - is_consistent: True if FAISS and MongoDB are in sync
            - faiss_count: Number of active vectors in FAISS
            - mongo_count: Number of active documents in MongoDB
            - in_mongo_not_faiss: List of IDs in MongoDB but not FAISS
            - in_faiss_not_mongo: List of IDs in FAISS but not MongoDB
            - checked_at: ISO timestamp of check
        """
        try:
            # Get all knowledge IDs from MongoDB
            loop = asyncio.get_running_loop()
            mongo_ids = await loop.run_in_executor(
                self._executor,
                self._get_active_mongo_ids
            )

            # Get all knowledge IDs from FAISS
            faiss_ids = self.embedding_service.get_active_ids()

            # Find discrepancies
            in_mongo_not_faiss = mongo_ids - faiss_ids
            in_faiss_not_mongo = faiss_ids - mongo_ids

            is_consistent = len(in_mongo_not_faiss) == 0 and len(in_faiss_not_mongo) == 0

            if not is_consistent:
                logger.warning(
                    f"FAISS/MongoDB inconsistency detected: "
                    f"{len(in_mongo_not_faiss)} in MongoDB not in FAISS, "
                    f"{len(in_faiss_not_mongo)} in FAISS not in MongoDB"
                )

            return {
                "is_consistent": is_consistent,
                "faiss_count": len(faiss_ids),
                "mongo_count": len(mongo_ids),
                "in_mongo_not_faiss": list(in_mongo_not_faiss)[:10],  # Limit for brevity
                "in_faiss_not_mongo": list(in_faiss_not_mongo)[:10],
                "in_mongo_not_faiss_count": len(in_mongo_not_faiss),
                "in_faiss_not_mongo_count": len(in_faiss_not_mongo),
                "checked_at": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Consistency check error: {e}", exc_info=True)
            return {
                "is_consistent": False,
                "error": str(e),
                "checked_at": datetime.utcnow().isoformat(),
            }

    def _get_active_mongo_ids(self) -> set[str]:
        """
        Get set of all active knowledge IDs from MongoDB.

        Returns:
            Set of knowledge IDs that are active (not deleted/deprecated, not expired)
        """
        now = datetime.utcnow()
        cursor = self.db.dakb_knowledge.find(
            {
                "embedding_indexed": True,
                "status": {"$nin": ["deprecated", "deleted"]},
                "$or": [
                    {"expires_at": None},
                    {"expires_at": {"$gt": now}}
                ]
            },
            {"knowledge_id": 1}
        )
        return set(doc["knowledge_id"] for doc in cursor)

    def get_sync_stats(self) -> dict:
        """
        Get current synchronization statistics.

        Returns:
            Dictionary with sync statistics including:
            - last_sync_at: ISO timestamp of last sync (or None)
            - last_rebuild_at: ISO timestamp of last rebuild (or None)
            - total_syncs: Total number of sync runs
            - total_rebuilds: Total number of index rebuilds
            - is_running: Whether periodic sync is currently running
            - current_deletion_count: Number of deleted vectors in FAISS
            - current_deletion_ratio: Ratio of deleted to total vectors
            - rebuild_threshold: Threshold ratio that triggers rebuild
        """
        if self.embedding_service.index is None:
            total_vectors = 0
        else:
            total_vectors = self.embedding_service.index.ntotal
        deletion_count = self.embedding_service.get_deletion_count()
        deletion_ratio = deletion_count / total_vectors if total_vectors > 0 else 0.0

        return {
            "last_sync_at": self._last_sync_at.isoformat() if self._last_sync_at else None,
            "last_rebuild_at": self._last_rebuild_at.isoformat() if self._last_rebuild_at else None,
            "total_syncs": self._total_syncs,
            "total_rebuilds": self._total_rebuilds,
            "is_running": self._running,
            "current_deletion_count": deletion_count,
            "current_total_vectors": total_vectors,
            "current_deletion_ratio": round(deletion_ratio, 4),
            "rebuild_threshold": self.REBUILD_THRESHOLD,
            "needs_rebuild": deletion_ratio > self.REBUILD_THRESHOLD,
        }

    async def cleanup(self):
        """
        Cleanup resources when shutting down.
        Stops periodic sync and shuts down thread pool executor.
        """
        self.stop_periodic_sync()

        if self._sync_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._sync_task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        self._executor.shutdown(wait=True)
        logger.info("FAISSMongoSyncService cleaned up")


# =============================================================================
# CONVENIENCE FACTORY
# =============================================================================

def create_sync_service(
    mongo_client: MongoClient,
    embedding_service: EmbeddingService,
    db_name: str = "dakb",
) -> FAISSMongoSyncService:
    """
    Factory function to create a FAISSMongoSyncService instance.

    Args:
        mongo_client: MongoDB client instance
        embedding_service: EmbeddingService instance
        db_name: Database name (default: dakb)

    Returns:
        Configured FAISSMongoSyncService instance

    Example:
        >>> from dakb.config import get_mongo_client
        >>> from backend.dakb_service.embeddings.embedding_service import EmbeddingService
        >>> from backend.dakb_service.embeddings.faiss_sync_service import create_sync_service
        >>>
        >>> client = get_mongo_client()
        >>> embedding_svc = EmbeddingService()
        >>> sync_svc = create_sync_service(client, embedding_svc)
        >>>
        >>> # Run one-time sync
        >>> import asyncio
        >>> result = asyncio.run(sync_svc.sync_deletions())
        >>>
        >>> # Or start periodic sync
        >>> sync_svc.start_periodic_sync(interval_seconds=3600)
    """
    return FAISSMongoSyncService(
        mongo_client=mongo_client,
        embedding_service=embedding_service,
        db_name=db_name,
    )
