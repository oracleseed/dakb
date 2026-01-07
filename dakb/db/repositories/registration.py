"""
DAKB Registration Repositories

Repository classes for invite token and registration audit management.
These classes handle CRUD operations for the invite-only registration system.

Version: 1.0
Created: 2025-12-11
Author: Backend Agent (Claude Opus 4.5)

Session Reference: sess_selfreg_v1_20251211
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.collection import Collection

from ..registration_schemas.registration import (
    # Local enums from registration module (avoid circular imports)
    DakbInviteToken,
    DakbRegistrationAudit,
    InviteTokenCreate,
    InviteTokenStatus,
    RegistrationAuditAction,
    generate_invite_token,
)

logger = logging.getLogger(__name__)


class InviteTokenRepository:
    """
    Repository for dakb_invite_tokens collection CRUD operations.

    Handles invite token creation, validation, consumption, and lifecycle management.
    Implements atomic operations for race condition prevention during registration.

    Key features:
    - Cryptographically secure token generation
    - Atomic token consumption (prevents race conditions)
    - Token expiration management
    - Admin-only token creation
    """

    def __init__(self, collection: Collection):
        """
        Initialize invite token repository.

        Args:
            collection: MongoDB collection for dakb_invite_tokens
        """
        self.collection = collection

    def create(
        self,
        created_by: str,
        data: InviteTokenCreate,
    ) -> DakbInviteToken:
        """
        Create a new invite token.

        Args:
            created_by: Admin agent ID creating the token
            data: Token creation data

        Returns:
            Created invite token

        Raises:
            DuplicateKeyError: If token already exists (extremely unlikely)
        """
        # Calculate expiration
        expires_at = datetime.utcnow() + timedelta(hours=data.expires_in_hours)

        # Create token record
        token = DakbInviteToken(
            invite_token=generate_invite_token(),
            created_by=created_by,
            for_agent_type=data.for_agent_type,
            for_agent_id_hint=data.for_agent_id_hint,
            purpose=data.purpose,
            granted_role=data.granted_role,
            granted_access_levels=data.granted_access_levels,
            pre_registered_alias=data.pre_registered_alias,
            pre_registered_alias_role=data.pre_registered_alias_role,
            expires_at=expires_at,
            admin_notes=data.admin_notes,
        )

        # Insert into MongoDB
        doc = token.model_dump()
        self.collection.insert_one(doc)

        logger.info(
            f"Invite token created: {token.invite_token[:20]}... by {created_by} "
            f"(expires: {expires_at.isoformat()})"
        )
        return token

    def get_by_token(self, invite_token: str) -> DakbInviteToken | None:
        """
        Get invite token by its token string.

        Args:
            invite_token: The invite token string

        Returns:
            DakbInviteToken or None if not found
        """
        doc = self.collection.find_one({"invite_token": invite_token})
        if doc:
            doc.pop("_id", None)
            return DakbInviteToken(**doc)
        return None

    def validate_and_consume(
        self,
        invite_token: str,
        agent_id: str,
    ) -> tuple[DakbInviteToken | None, str | None]:
        """
        Atomically validate and consume an invite token.

        Uses MongoDB findOneAndUpdate to prevent race conditions
        where multiple registrations try to use the same token.

        Args:
            invite_token: The invite token to validate
            agent_id: Agent ID attempting to use the token

        Returns:
            Tuple of (token, error_message):
            - If valid: (DakbInviteToken, None)
            - If invalid: (None, error_message)
        """
        now = datetime.utcnow()

        # Atomic update: only succeeds if token is ACTIVE and not expired
        result = self.collection.find_one_and_update(
            {
                "invite_token": invite_token,
                "status": InviteTokenStatus.ACTIVE.value,
                "expires_at": {"$gt": now}
            },
            {
                "$set": {
                    "status": InviteTokenStatus.USED.value,
                    "used_by_agent_id": agent_id,
                    "used_at": now
                }
            },
            return_document=ReturnDocument.AFTER
        )

        if result:
            result.pop("_id", None)
            logger.info(
                f"Invite token consumed: {invite_token[:20]}... by {agent_id}"
            )
            return DakbInviteToken(**result), None

        # Token not found, expired, or already used - determine why
        token_doc = self.collection.find_one({"invite_token": invite_token})

        if token_doc is None:
            return None, "invalid_token: Invite token is invalid or does not exist."

        status = token_doc.get("status")
        if status == InviteTokenStatus.USED.value:
            used_by = token_doc.get("used_by_agent_id", "unknown")
            return None, f"token_already_used: Invite token was already used by agent '{used_by}'."

        if status == InviteTokenStatus.REVOKED.value:
            return None, "token_revoked: Invite token has been revoked by an admin."

        expires_at = token_doc.get("expires_at")
        if expires_at and expires_at <= now:
            return None, f"token_expired: Invite token expired at {expires_at.isoformat()}."

        return None, f"token_invalid_status: Invite token has status '{status}' and cannot be used."

    def rollback_consumption(self, invite_token: str) -> bool:
        """
        Rollback a token consumption (used when registration fails after token consumed).

        This is a safety mechanism to prevent tokens from being wasted
        if registration fails after the token has been consumed.

        Args:
            invite_token: The token to rollback

        Returns:
            True if rollback successful
        """
        result = self.collection.update_one(
            {
                "invite_token": invite_token,
                "status": InviteTokenStatus.USED.value
            },
            {
                "$set": {
                    "status": InviteTokenStatus.ACTIVE.value,
                    "used_by_agent_id": None,
                    "used_at": None
                }
            }
        )

        if result.modified_count > 0:
            logger.info(f"Token consumption rolled back: {invite_token[:20]}...")
            return True

        logger.warning(f"Failed to rollback token: {invite_token[:20]}...")
        return False

    def revoke(self, invite_token: str, revoked_by: str) -> bool:
        """
        Revoke an invite token (admin action).

        Can only revoke ACTIVE tokens. USED tokens cannot be revoked
        (the agent is already registered).

        Args:
            invite_token: Token to revoke
            revoked_by: Admin agent ID performing revocation

        Returns:
            True if revoked successfully
        """
        result = self.collection.update_one(
            {
                "invite_token": invite_token,
                "status": InviteTokenStatus.ACTIVE.value
            },
            {
                "$set": {
                    "status": InviteTokenStatus.REVOKED.value,
                    "admin_notes": f"Revoked by {revoked_by} at {datetime.utcnow().isoformat()}"
                }
            }
        )

        if result.modified_count > 0:
            logger.info(f"Token revoked: {invite_token[:20]}... by {revoked_by}")
            return True

        return False

    def list_tokens(
        self,
        status: InviteTokenStatus | None = None,
        created_by: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[DakbInviteToken]:
        """
        List invite tokens with optional filtering.

        Args:
            status: Filter by status
            created_by: Filter by creator
            limit: Maximum results
            skip: Number to skip

        Returns:
            List of invite tokens
        """
        query: dict[str, Any] = {}

        if status:
            query["status"] = status.value
        if created_by:
            query["created_by"] = created_by

        cursor = self.collection.find(query).sort(
            "created_at", -1
        ).skip(skip).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbInviteToken(**doc))

        return results

    def count_tokens(
        self,
        status: InviteTokenStatus | None = None,
        created_by: str | None = None,
    ) -> int:
        """
        Count tokens with optional filtering.

        Args:
            status: Filter by status
            created_by: Filter by creator

        Returns:
            Token count
        """
        query: dict[str, Any] = {}

        if status:
            query["status"] = status.value
        if created_by:
            query["created_by"] = created_by

        return self.collection.count_documents(query)

    def expire_old_tokens(self) -> int:
        """
        Mark expired tokens as EXPIRED.

        This is a background cleanup operation. The TTL index will
        eventually delete these, but this marks them for queries.

        Returns:
            Number of tokens marked as expired
        """
        now = datetime.utcnow()

        result = self.collection.update_many(
            {
                "status": InviteTokenStatus.ACTIVE.value,
                "expires_at": {"$lt": now}
            },
            {
                "$set": {"status": InviteTokenStatus.EXPIRED.value}
            }
        )

        if result.modified_count > 0:
            logger.info(f"Expired {result.modified_count} invite tokens")

        return result.modified_count

    def delete_token(self, invite_token: str) -> bool:
        """
        Permanently delete a token (hard delete).

        Use with caution - prefer revoke() for audit trail.

        Args:
            invite_token: Token to delete

        Returns:
            True if deleted
        """
        result = self.collection.delete_one({"invite_token": invite_token})
        if result.deleted_count > 0:
            logger.info(f"Token deleted: {invite_token[:20]}...")
            return True
        return False


class RegistrationAuditRepository:
    """
    Repository for dakb_registration_audit collection operations.

    Handles audit logging for registration-specific events.
    All entries have a 90-day TTL enforced by MongoDB index.

    Key features:
    - Specialized registration audit queries
    - Automatic 90-day TTL on entries
    - Support for security monitoring
    """

    def __init__(self, collection: Collection):
        """
        Initialize registration audit repository.

        Args:
            collection: MongoDB collection for dakb_registration_audit
        """
        self.collection = collection

    def create(self, audit: DakbRegistrationAudit) -> DakbRegistrationAudit:
        """
        Create an audit log entry.

        Args:
            audit: Audit entry to create

        Returns:
            Created audit entry
        """
        doc = audit.model_dump()
        self.collection.insert_one(doc)

        log_level = logging.INFO if audit.success else logging.WARNING
        logger.log(
            log_level,
            f"Registration audit: {audit.action.value} by {audit.actor_agent_id} "
            f"(success={audit.success})"
        )

        return audit

    def log_invite_created(
        self,
        admin_id: str,
        invite_token: str,
        details: dict | None = None,
        ip_address: str | None = None,
    ) -> DakbRegistrationAudit:
        """
        Log invite token creation.

        Args:
            admin_id: Admin who created the invite
            invite_token: The created token
            details: Additional details
            ip_address: Admin's IP address

        Returns:
            Created audit entry
        """
        audit = DakbRegistrationAudit(
            action=RegistrationAuditAction.INVITE_CREATED,
            actor_agent_id=admin_id,
            actor_ip=ip_address,
            target_token=invite_token,
            details=details or {},
            success=True,
        )
        return self.create(audit)

    def log_agent_registered(
        self,
        agent_id: str,
        invite_token: str,
        details: dict | None = None,
        ip_address: str | None = None,
    ) -> DakbRegistrationAudit:
        """
        Log successful agent registration.

        Args:
            agent_id: Newly registered agent ID
            invite_token: Token used for registration
            details: Registration details
            ip_address: Agent's IP address

        Returns:
            Created audit entry
        """
        audit = DakbRegistrationAudit(
            action=RegistrationAuditAction.AGENT_REGISTERED,
            actor_agent_id=agent_id,
            actor_ip=ip_address,
            target_token=invite_token,
            target_agent_id=agent_id,
            details=details or {},
            success=True,
        )
        return self.create(audit)

    def log_registration_failed(
        self,
        agent_id: str,
        invite_token: str,
        error_message: str,
        details: dict | None = None,
        ip_address: str | None = None,
    ) -> DakbRegistrationAudit:
        """
        Log failed registration attempt.

        Args:
            agent_id: Attempted agent ID
            invite_token: Token used (if any)
            error_message: Why registration failed
            details: Additional details
            ip_address: Request IP address

        Returns:
            Created audit entry
        """
        audit = DakbRegistrationAudit(
            action=RegistrationAuditAction.REGISTRATION_FAILED,
            actor_agent_id=agent_id,
            actor_ip=ip_address,
            target_token=invite_token,
            target_agent_id=agent_id,
            details=details or {},
            success=False,
            error_message=error_message,
        )
        return self.create(audit)

    def log_agent_revoked(
        self,
        admin_id: str,
        agent_id: str,
        reason: str,
        details: dict | None = None,
        ip_address: str | None = None,
    ) -> DakbRegistrationAudit:
        """
        Log agent revocation.

        Args:
            admin_id: Admin who revoked access
            agent_id: Agent whose access was revoked
            reason: Reason for revocation
            details: Additional details
            ip_address: Admin's IP address

        Returns:
            Created audit entry
        """
        audit = DakbRegistrationAudit(
            action=RegistrationAuditAction.AGENT_REVOKED,
            actor_agent_id=admin_id,
            actor_ip=ip_address,
            target_agent_id=agent_id,
            details={**(details or {}), "reason": reason},
            success=True,
        )
        return self.create(audit)

    def log_invite_revoked(
        self,
        admin_id: str,
        invite_token: str,
        reason: str | None = None,
        ip_address: str | None = None,
    ) -> DakbRegistrationAudit:
        """
        Log invite token revocation.

        Args:
            admin_id: Admin who revoked the token
            invite_token: The revoked token
            reason: Reason for revocation
            ip_address: Admin's IP address

        Returns:
            Created audit entry
        """
        audit = DakbRegistrationAudit(
            action=RegistrationAuditAction.INVITE_REVOKED,
            actor_agent_id=admin_id,
            actor_ip=ip_address,
            target_token=invite_token,
            details={"reason": reason} if reason else {},
            success=True,
        )
        return self.create(audit)

    def find_by_agent(
        self,
        agent_id: str,
        action: RegistrationAuditAction | None = None,
        limit: int = 100,
    ) -> list[DakbRegistrationAudit]:
        """
        Find audit entries by agent ID.

        Args:
            agent_id: Agent ID (actor or target)
            action: Optional action filter
            limit: Maximum results

        Returns:
            List of audit entries
        """
        query: dict[str, Any] = {
            "$or": [
                {"actor_agent_id": agent_id},
                {"target_agent_id": agent_id}
            ]
        }

        if action:
            query["action"] = action.value

        cursor = self.collection.find(query).sort(
            "timestamp", -1
        ).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbRegistrationAudit(**doc))

        return results

    def find_by_token(self, invite_token: str) -> list[DakbRegistrationAudit]:
        """
        Find all audit entries for a specific invite token.

        Args:
            invite_token: The token to search for

        Returns:
            List of audit entries
        """
        cursor = self.collection.find(
            {"target_token": invite_token}
        ).sort("timestamp", -1)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbRegistrationAudit(**doc))

        return results

    def find_failures(
        self,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[DakbRegistrationAudit]:
        """
        Find failed registration attempts (for security monitoring).

        Args:
            since: Only get failures after this time
            limit: Maximum results

        Returns:
            List of failed audit entries
        """
        query: dict[str, Any] = {"success": False}

        if since:
            query["timestamp"] = {"$gte": since}

        cursor = self.collection.find(query).sort(
            "timestamp", -1
        ).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbRegistrationAudit(**doc))

        return results

    def list_all(
        self,
        action: RegistrationAuditAction | None = None,
        agent_id: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[DakbRegistrationAudit]:
        """
        List audit entries with optional filtering.

        Args:
            action: Filter by action type
            agent_id: Filter by actor or target agent
            limit: Maximum results
            skip: Number to skip

        Returns:
            List of audit entries
        """
        query: dict[str, Any] = {}

        if action:
            query["action"] = action.value

        if agent_id:
            query["$or"] = [
                {"actor_agent_id": agent_id},
                {"target_agent_id": agent_id}
            ]

        cursor = self.collection.find(query).sort(
            "timestamp", -1
        ).skip(skip).limit(limit)

        results = []
        for doc in cursor:
            doc.pop("_id", None)
            results.append(DakbRegistrationAudit(**doc))

        return results

    def count_entries(
        self,
        action: RegistrationAuditAction | None = None,
        agent_id: str | None = None,
    ) -> int:
        """
        Count audit entries with optional filtering.

        Args:
            action: Filter by action type
            agent_id: Filter by actor or target agent

        Returns:
            Entry count
        """
        query: dict[str, Any] = {}

        if action:
            query["action"] = action.value

        if agent_id:
            query["$or"] = [
                {"actor_agent_id": agent_id},
                {"target_agent_id": agent_id}
            ]

        return self.collection.count_documents(query)

    def get_statistics(self) -> dict:
        """
        Get registration audit statistics.

        Returns:
            Dictionary with counts by action type and success rate
        """
        # Count by action
        action_pipeline = [
            {"$group": {"_id": "$action", "count": {"$sum": 1}}}
        ]
        by_action = {}
        for doc in self.collection.aggregate(action_pipeline):
            if doc["_id"]:
                by_action[doc["_id"]] = doc["count"]

        # Count successes/failures
        success_count = self.collection.count_documents({"success": True})
        failure_count = self.collection.count_documents({"success": False})
        total = success_count + failure_count

        return {
            "total_entries": total,
            "by_action": by_action,
            "success_count": success_count,
            "failure_count": failure_count,
            "success_rate": success_count / total if total > 0 else 0.0,
        }
