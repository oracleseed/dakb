"""ChatSessionManager — MongoDB CRUD for the ``dakb_chat_sessions`` collection.

Manages chat session lifecycle: create, lookup, invite/uninvite agents,
and activity tracking via composite chat IDs (``platform:external_chat_id``).
"""

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ChatSessionManager:
    """CRUD operations for the dakb_chat_sessions collection."""

    def __init__(self, collection) -> None:
        self._collection = collection

    @staticmethod
    def make_composite_id(platform: str, external_chat_id: str) -> str:
        """Build a composite chat ID from platform and external chat ID."""
        return f"{platform}:{external_chat_id}"

    async def create_session(
        self,
        platform: str,
        external_chat_id: str,
        external_user_id: str,
        conversation_mode: str = "direct",
    ) -> dict:
        """Create a new chat session document and insert it."""
        now = datetime.now(timezone.utc)
        doc = {
            "session_id": f"csess_{uuid.uuid4().hex[:12]}",
            "platform": platform,
            "composite_chat_id": self.make_composite_id(platform, external_chat_id),
            "external_chat_id": external_chat_id,
            "external_user_id": external_user_id,
            "invited_agents": [],
            "conversation_mode": conversation_mode,
            "relay_config": {
                "mirror_agent_messages": False,
                "allow_user_interrupt": True,
                "relay_filter": "none",
            },
            "created_at": now,
            "last_active": now,
        }
        await self._collection.insert_one(doc)
        return doc

    async def get_session(self, composite_chat_id: str) -> dict | None:
        """Look up a session by composite chat ID. Returns doc or None."""
        return await self._collection.find_one(
            {"composite_chat_id": composite_chat_id}
        )

    async def get_or_create(
        self,
        platform: str,
        external_chat_id: str,
        external_user_id: str,
    ) -> dict:
        """Return existing session or create a new one."""
        composite = self.make_composite_id(platform, external_chat_id)
        existing = await self.get_session(composite)
        if existing is not None:
            return existing
        return await self.create_session(platform, external_chat_id, external_user_id)

    async def invite_agent(self, composite_chat_id: str, agent_name: str) -> bool:
        """Add agent to invited_agents via $addToSet. Returns True if newly added."""
        result = await self._collection.update_one(
            {"composite_chat_id": composite_chat_id},
            {"$addToSet": {"invited_agents": agent_name}},
        )
        return result.modified_count > 0

    async def uninvite_agent(self, composite_chat_id: str, agent_name: str) -> bool:
        """Remove agent from invited_agents via $pull. Returns True if removed."""
        result = await self._collection.update_one(
            {"composite_chat_id": composite_chat_id},
            {"$pull": {"invited_agents": agent_name}},
        )
        return result.modified_count > 0

    async def touch(self, composite_chat_id: str) -> None:
        """Update last_active timestamp to now."""
        await self._collection.update_one(
            {"composite_chat_id": composite_chat_id},
            {"$set": {"last_active": datetime.now(timezone.utc)}},
        )

    async def get_invited_agents(self, composite_chat_id: str) -> list[str]:
        """Return the list of invited agent names for a session."""
        session = await self.get_session(composite_chat_id)
        if session is None:
            return []
        return session.get("invited_agents", [])
