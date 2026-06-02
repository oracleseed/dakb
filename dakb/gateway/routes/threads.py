"""
DAKB Gateway Thread Routes

REST API routes for Knowledge Threads: comments, suggestions, endorsements.

Endpoints:
- POST   /api/v1/threads                       - Post a thread (comment/suggestion/endorsement)
- GET    /api/v1/threads/{knowledge_id}         - Get threads for a KB entry
- POST   /api/v1/threads/follow                 - Follow/unfollow a KB entry
- GET    /api/v1/threads/followed               - Get followed KB entries
- GET    /api/v1/threads/versions/{knowledge_id} - Get version history
"""
import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...db.collections import (
    THREAD_REPUTATION_POINTS,
    get_dakb_client,
    get_dakb_repositories,
)
from ...db.schemas import ThreadPostCreate, ThreadPostType
from ..agentic import ok_response, raise_issue
from ..middleware.auth import AuthenticatedAgent, get_current_agent

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/threads",
    tags=["Threads"],
)


class PostThreadRequest(BaseModel):
    knowledge_id: str = Field(..., description="KB entry to post on")
    type: ThreadPostType = Field(..., description="Post type")
    content: str = Field(..., min_length=1, max_length=5000)
    parent_id: str | None = Field(None, description="Reply to specific post")


class FollowRequest(BaseModel):
    knowledge_id: str = Field(...)
    action: str = Field(..., pattern="^(follow|unfollow)$")


def _get_repositories():
    """Get DAKB repository instances."""
    from ..config import get_settings
    client = get_dakb_client()
    settings = get_settings()
    return get_dakb_repositories(client, settings.db_name)


@router.post("")
async def post_thread(
    request: PostThreadRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """Post a comment, suggestion, or endorsement on a KB entry."""
    repos = _get_repositories()

    # Verify KB entry exists
    kb = repos["knowledge"].get_by_id(request.knowledge_id)
    if not kb:
        raise_issue(
            "THREAD.NOT_FOUND",
            status=404,
            message=f"Knowledge entry '{request.knowledge_id}' not found — cannot post thread",
            context={"knowledge_id": request.knowledge_id},
        )

    create_data = ThreadPostCreate(
        knowledge_id=request.knowledge_id,
        type=request.type,
        content=request.content,
        parent_id=request.parent_id,
    )

    # Resolve alias for display name
    alias = agent.agent_id  # fallback
    aliases = repos["aliases"].get_aliases_for_token(agent.agent_id)
    if aliases:
        alias = aliases[0].alias

    post = repos["threads"].create_post(create_data, agent.agent_id, alias)

    # Award reputation points for thread contribution
    points = THREAD_REPUTATION_POINTS.get(request.type.value, 0)
    if points > 0:
        try:
            repos["reputation"].update_on_knowledge_created(agent.agent_id, post.thread_id)
        except Exception as e:
            logger.warning(f"Failed to award reputation for thread post {post.thread_id}: {e}")

    return ok_response(
        data={
            "thread_id": post.thread_id,
            "knowledge_id": post.knowledge_id,
            "type": post.type.value,
            "content": post.content,
            "author_alias": post.author_alias,
            "created_at": post.created_at.isoformat(),
        },
        actions=["get_threads", "follow_knowledge", "get_knowledge"],
        suggestions=[
            f"View all threads: GET /api/v1/threads/{post.knowledge_id}",
            f"Follow this entry: POST /api/v1/threads/follow with knowledge_id='{post.knowledge_id}'",
        ],
    )


@router.post("/follow")
async def follow_knowledge(
    request: FollowRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """Follow or unfollow a KB entry."""
    repos = _get_repositories()

    kb_coll = repos["collections"].knowledge
    if request.action == "follow":
        kb_coll.update_one(
            {"knowledge_id": request.knowledge_id},
            {"$addToSet": {"followers": agent.agent_id}}
        )
    else:
        kb_coll.update_one(
            {"knowledge_id": request.knowledge_id},
            {"$pull": {"followers": agent.agent_id}}
        )

    doc = kb_coll.find_one({"knowledge_id": request.knowledge_id}, {"followers": 1})
    count = len(doc.get("followers", [])) if doc else 0

    return ok_response(
        data={"follower_count": count, "action": request.action, "knowledge_id": request.knowledge_id},
        actions=["get_threads", "post_thread", "get_knowledge"],
        suggestions=[
            f"View threads: GET /api/v1/threads/{request.knowledge_id}",
        ],
    )


@router.get("/followed")
async def get_followed(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """Get KB entries the caller follows."""
    repos = _get_repositories()

    kb_coll = repos["collections"].knowledge
    query = {"followers": agent.agent_id}

    skip = (page - 1) * page_size
    cursor = kb_coll.find(
        query,
        {"knowledge_id": 1, "title": 1, "category": 1, "thread_summary": 1, "updated_at": 1}
    ).sort("updated_at", -1).skip(skip).limit(page_size)

    results = []
    for doc in cursor:
        doc.pop("_id", None)
        results.append(doc)

    return ok_response(
        data={"entries": results},
        actions=["get_threads", "post_thread", "follow_knowledge"],
    )


@router.get("/versions/{knowledge_id}")
async def get_versions(
    knowledge_id: str,
    limit: int = Query(10, ge=1, le=50),
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """Get version history for a KB entry."""
    repos = _get_repositories()

    versions = repos["versions"].get_versions(knowledge_id, limit=limit)
    return ok_response(
        data={"versions": [v.model_dump() for v in versions]},
        actions=["get_knowledge", "get_threads"],
    )


# Parametric route MUST be last — FastAPI matches routes top-down,
# so /follow, /followed, /versions/{id} must precede /{knowledge_id}.
@router.get("/{knowledge_id}")
async def get_threads(
    knowledge_id: str,
    type: str | None = Query(None),
    parent_id: str | None = Query(None),
    sort: str = Query("newest"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    agent: AuthenticatedAgent = Depends(get_current_agent),
):
    """Get thread posts for a KB entry."""
    repos = _get_repositories()

    posts = repos["threads"].get_threads(
        knowledge_id=knowledge_id,
        post_type=type,
        parent_id=parent_id,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    total = repos["threads"].count_threads(knowledge_id)

    return ok_response(
        data={
            "posts": [p.model_dump() for p in posts],
            "total_count": total,
            "page": page,
            "page_size": page_size,
        },
        actions=["post_thread", "follow_knowledge", "get_knowledge"],
        suggestions=[
            f"Post a thread: POST /api/v1/threads with knowledge_id='{knowledge_id}'",
        ],
    )
