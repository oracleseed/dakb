"""Bridge REST API routes -- link, unlink, pending, send, ack, status.

SECURITY: every route requires a valid DAKB token via the router-level
`dependencies=[Depends(get_current_agent)]` guard, and ownership of the named
session_id is verified against the authenticated agent before any read/write.
The agent_id persisted on /link is bound to the token, never trusted from the
request body -- this closes the unauthenticated IDOR that would otherwise let
any caller read the linked composite_chat_id list (/pending, /status) for an
arbitrary session_id.
"""
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from ..gateway.middleware.auth import AuthenticatedAgent, get_current_agent
from .models import BridgeLink, BridgeStatus

logger = logging.getLogger(__name__)


# --- Request/Response schemas ---

class LinkRequest(BaseModel):
    session_id: str
    # agent_id is OPTIONAL and IGNORED -- the persisted owner is bound to the
    # authenticated token (req.agent_id is never trusted). Kept for backward
    # compatibility with existing callers that still send it.
    agent_id: str | None = None
    composite_chat_id: str
    platform: str
    linked_by: str

class UnlinkRequest(BaseModel):
    session_id: str
    composite_chat_id: str

class SendRequest(BaseModel):
    session_id: str
    text: str

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v):
        if not v.strip():
            raise ValueError("text must not be empty")
        return v

class AckRequest(BaseModel):
    session_id: str
    msg_id: str


def create_bridge_router(queue, links_collection, redis, outbound_consumer=None) -> APIRouter:
    """Factory: creates bridge router with injected dependencies.

    All routes are guarded by `Depends(get_current_agent)` (router-level), so an
    unauthenticated caller gets 401 before any handler runs. Handlers then call
    `_require_session_owner(...)` to enforce that the authenticated agent owns the
    target session_id (i.e. there is an active BridgeLink whose agent_id matches
    the token's agent_id).
    """
    router = APIRouter(
        prefix="/bridge",
        tags=["Session Bridge"],
        # Auth on EVERY bridge route. A missing/invalid token -> 401 here, before
        # the handler body executes (mirrors knowledge/moderation/aliases routers).
        dependencies=[Depends(get_current_agent)],
    )

    async def _require_session_owner(session_id: str, agent: AuthenticatedAgent):
        """Raise 403 unless `agent` owns `session_id`.

        Ownership = there exists an active BridgeLink with this session_id whose
        agent_id equals the authenticated token's agent_id. Prevents the IDOR
        where any authenticated agent could read/act on another agent's session.
        """
        owner_link = await links_collection.find_one(
            {"session_id": session_id, "agent_id": agent.agent_id, "active": True}
        )
        if not owner_link:
            raise HTTPException(
                status_code=403,
                detail="Access denied: session is not owned by the authenticated agent.",
            )

    @router.post("/link", status_code=201)
    async def link_session(
        req: LinkRequest,
        agent: AuthenticatedAgent = Depends(get_current_agent),
    ):
        """Link an agent session to an external chat.

        The owner (agent_id) is bound to the authenticated token; req.agent_id is
        ignored. This is the route that *establishes* ownership, so no prior
        ownership check is performed.
        """
        link = BridgeLink(
            session_id=req.session_id,
            agent_id=agent.agent_id,  # bound to token, NOT req.agent_id
            composite_chat_id=req.composite_chat_id,
            platform=req.platform,
            linked_by=req.linked_by,
        )
        await links_collection.insert_one(link.model_dump())
        return {"status": "linked", "session_id": req.session_id, "chat": req.composite_chat_id}

    @router.post("/unlink")
    async def unlink_session(
        req: UnlinkRequest,
        agent: AuthenticatedAgent = Depends(get_current_agent),
    ):
        """Unlink a chat from an agent session (owner only)."""
        await _require_session_owner(req.session_id, agent)
        await links_collection.update_one(
            {"session_id": req.session_id, "composite_chat_id": req.composite_chat_id},
            {"$set": {"active": False}},
        )
        return {"status": "unlinked"}

    @router.get("/pending")
    async def get_pending(
        session_id: str = Query(...),
        agent: AuthenticatedAgent = Depends(get_current_agent),
    ):
        """Get pending inbound messages for a session (owner only)."""
        await _require_session_owner(session_id, agent)
        messages = await queue.get_pending(session_id)
        return {"messages": messages, "count": len(messages)}

    @router.post("/send")
    async def send_message(
        req: SendRequest,
        agent: AuthenticatedAgent = Depends(get_current_agent),
    ):
        """Send a message from agent to all linked chats (owner only)."""
        await _require_session_owner(req.session_id, agent)
        cursor = links_collection.find({"session_id": req.session_id, "active": True})
        links = await cursor.to_list(length=100)
        if not links:
            raise HTTPException(status_code=404, detail="No linked chats for this session")

        sent_to = []
        for link in links:
            if outbound_consumer:
                # OutboundConsumer.deliver expects a params dict
                # (composite_chat_id / content / from_agent), matching the
                # stream-driven delivery path in process_one().
                await outbound_consumer.deliver(
                    {
                        "composite_chat_id": link["composite_chat_id"],
                        "content": req.text,
                        "from_agent": getattr(agent, "agent_id", None)
                        or getattr(agent, "token_id", "agent"),
                    }
                )
            sent_to.append(link["composite_chat_id"])

        return {"status": "sent", "sent_to": sent_to}

    @router.post("/ack")
    async def ack_message(
        req: AckRequest,
        agent: AuthenticatedAgent = Depends(get_current_agent),
    ):
        """Acknowledge receipt of messages up to msg_id (owner only)."""
        await _require_session_owner(req.session_id, agent)
        last_seen_key = f"bridge:last_seen:{req.session_id}"
        await redis.set(last_seen_key, req.msg_id)
        return {"status": "acked", "last_seen": req.msg_id}

    @router.get("/status")
    async def get_status(
        session_id: str = Query(...),
        agent: AuthenticatedAgent = Depends(get_current_agent),
    ):
        """Get bridge status for a session (owner only)."""
        await _require_session_owner(session_id, agent)
        heartbeat_key = f"bridge:heartbeat:{session_id}"
        hb_exists = await redis.exists(heartbeat_key)
        hb_age = 0.0
        if hb_exists:
            hb_val = await redis.get(heartbeat_key)
            if hb_val:
                hb_age = time.time() - float(hb_val if isinstance(hb_val, str) else hb_val.decode())

        cursor = links_collection.find({"session_id": session_id, "active": True})
        links = await cursor.to_list(length=100)
        chat_ids = [link["composite_chat_id"] for link in links]

        inbox_depth = await queue.get_inbox_depth(session_id)
        queue_depth = await queue.get_queue_depth(session_id)

        return BridgeStatus(
            session_id=session_id,
            bridge_online=bool(hb_exists),
            heartbeat_age_seconds=round(hb_age, 1),
            linked_chats=chat_ids,
            inbox_depth=inbox_depth,
            queue_depth=queue_depth,
        ).model_dump()

    return router
