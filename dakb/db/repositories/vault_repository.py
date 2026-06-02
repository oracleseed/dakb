"""
DAKB Vault File Repository

Repository class for dakb_vault_files collection CRUD operations.
Handles file metadata storage, retrieval, status updates, and lifecycle management.

Version: 1.0
Created: 2026-03-27
"""

import logging
from datetime import datetime, timedelta

from pymongo.collection import Collection

from ..schemas import VaultFile, VaultFileStatus

logger = logging.getLogger(__name__)


class VaultFileRepository:
    """
    Repository for dakb_vault_files collection CRUD operations.

    Handles vault file metadata storage, retrieval, status transitions,
    budget tracking, and knowledge counter synchronization.
    """

    def __init__(self, collection: Collection, knowledge_collection: Collection):
        """
        Initialize vault file repository.

        Args:
            collection: MongoDB collection for dakb_vault_files
            knowledge_collection: MongoDB collection for dakb_knowledge (for counter updates)
        """
        self.collection = collection
        self.knowledge_collection = knowledge_collection

    def create(self, vault_file: VaultFile) -> VaultFile:
        """
        Insert a new vault file record.

        Args:
            vault_file: VaultFile model instance

        Returns:
            The inserted VaultFile
        """
        doc = vault_file.model_dump()
        self.collection.insert_one(doc)
        logger.info(f"Created vault file: {vault_file.file_id} for {vault_file.knowledge_id}")
        return vault_file

    def get_by_id(self, file_id: str) -> VaultFile | None:
        """
        Get vault file by file_id.

        Args:
            file_id: Unique file identifier

        Returns:
            VaultFile or None if not found
        """
        doc = self.collection.find_one({"file_id": file_id})
        if doc:
            doc.pop("_id", None)
            return VaultFile(**doc)
        return None

    def get_by_knowledge_id(self, knowledge_id: str) -> list[VaultFile]:
        """
        Get all active vault files for a knowledge entry.

        Args:
            knowledge_id: Knowledge entry identifier

        Returns:
            List of active VaultFile instances
        """
        docs = list(self.collection.find({
            "knowledge_id": knowledge_id,
            "status": VaultFileStatus.ACTIVE.value,
        }))
        results = []
        for doc in docs:
            doc.pop("_id", None)
            results.append(VaultFile(**doc))
        return results

    def update_status(self, file_id: str, status: VaultFileStatus) -> bool:
        """
        Update the status of a vault file.

        Args:
            file_id: File identifier
            status: New status

        Returns:
            True if updated, False if not found
        """
        result = self.collection.update_one(
            {"file_id": file_id},
            {"$set": {"status": status.value}},
        )
        return result.modified_count > 0

    def soft_delete(self, file_id: str, ttl_days: int = 30) -> bool:
        """
        Soft-delete a vault file by setting status=DELETED and purge_after.

        Caller should call ``update_knowledge_counters()`` after soft_delete
        to sync the parent knowledge entry's vault_file_count and
        vault_total_size_bytes. Kept separate for batching multiple deletes.

        Args:
            file_id: File identifier
            ttl_days: Days until permanent purge (default 30)

        Returns:
            True if updated, False if not found
        """
        now = datetime.utcnow()
        result = self.collection.update_one(
            {"file_id": file_id},
            {"$set": {
                "status": VaultFileStatus.DELETED.value,
                "deleted_at": now,
                "purge_after": now + timedelta(days=ttl_days),
            }},
        )
        return result.modified_count > 0

    def get_budget_usage(self, knowledge_id: str) -> dict:
        """
        Get file count and total size for a knowledge entry (active files only).

        Args:
            knowledge_id: Knowledge entry identifier

        Returns:
            Dict with file_count and total_size_bytes
        """
        pipeline = [
            {"$match": {"knowledge_id": knowledge_id, "status": VaultFileStatus.ACTIVE.value}},
            {"$group": {
                "_id": None,
                "file_count": {"$sum": 1},
                "total_size_bytes": {"$sum": "$size_bytes"},
            }},
        ]
        results = list(self.collection.aggregate(pipeline))
        if results:
            return {
                "file_count": results[0]["file_count"],
                "total_size_bytes": results[0]["total_size_bytes"],
            }
        return {"file_count": 0, "total_size_bytes": 0}

    def get_expired_files(self) -> list[VaultFile]:
        """
        Get soft-deleted files that have passed their purge_after date.

        Returns:
            List of VaultFile instances ready for permanent deletion
        """
        now = datetime.utcnow()
        docs = list(self.collection.find({
            "status": VaultFileStatus.DELETED.value,
            "purge_after": {"$lte": now},
        }))
        results = []
        for doc in docs:
            doc.pop("_id", None)
            results.append(VaultFile(**doc))
        return results

    def count_by_status(self) -> dict:
        """
        Count vault files grouped by status.

        Returns:
            Dict mapping status string to count
        """
        pipeline = [
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        results = list(self.collection.aggregate(pipeline))
        return {r["_id"]: r["count"] for r in results}

    def update_knowledge_counters(self, knowledge_id: str) -> None:
        """
        Recalculate and update vault file counters on the parent knowledge entry.

        Args:
            knowledge_id: Knowledge entry identifier
        """
        usage = self.get_budget_usage(knowledge_id)
        file_count = usage["file_count"]
        total_size = usage["total_size_bytes"]

        self.knowledge_collection.update_one(
            {"knowledge_id": knowledge_id},
            {"$set": {
                "has_vault_files": file_count > 0,
                "vault_file_count": file_count,
                "vault_total_size_bytes": total_size,
            }},
        )
