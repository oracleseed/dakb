"""
DAKB Gateway Knowledge Routes

REST API routes for knowledge CRUD operations and semantic search.
All routes require JWT authentication and respect access control levels.

Version: 1.2
Created: 2025-12-07
Updated: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Route Ordering (ISS-040 Fix):
Static routes are defined BEFORE parametric routes to prevent FastAPI
from matching "/stats" as knowledge_id="stats".

Endpoints (Static Routes - defined first):
- POST   /api/v1/knowledge              - Create knowledge entry
- GET    /api/v1/knowledge/search       - Semantic search
- POST   /api/v1/knowledge/bulk         - Bulk create knowledge entries
- GET    /api/v1/knowledge/by-tags      - List by tags
- GET    /api/v1/knowledge/stats        - Get statistics
- POST   /api/v1/knowledge/cleanup-expired - Cleanup expired (admin)

Endpoints (Parametric Routes - defined after static routes):
- GET    /api/v1/knowledge/{id}         - Get knowledge by ID
- PUT    /api/v1/knowledge/{id}         - Update knowledge
- DELETE /api/v1/knowledge/{id}         - Soft delete knowledge
- POST   /api/v1/knowledge/{id}/vote    - Vote on knowledge
- GET    /api/v1/knowledge/{id}/related - Find related entries
- GET    /api/v1/knowledge              - List knowledge (with filters)
"""

import time
import logging
from datetime import datetime
from typing import Optional, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..config import get_settings
from ..middleware.auth import (
    AuthenticatedAgent,
    get_current_agent,
    check_rate_limit,
    AccessChecker,
)
from ...db import (
    # Schemas
    DakbKnowledge,
    KnowledgeCreate,
    KnowledgeUpdate,
    VoteCreate,
    KnowledgeSource,
    KnowledgeResponse,
    SearchResults,
    # Enums
    AccessLevel,
    KnowledgeStatus,
    Category,
    ContentType,
    AuditAction,
    ResourceType,
    AgentType,
    # Repositories
    get_dakb_repositories,
)
from ...db.collections import get_dakb_client

logger = logging.getLogger(__name__)

# =============================================================================
# ROUTER
# =============================================================================

router = APIRouter(
    prefix="/api/v1/knowledge",
    tags=["Knowledge"],
    dependencies=[Depends(check_rate_limit)],  # Rate limiting on all routes
)


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================

class CreateKnowledgeRequest(BaseModel):
    """Request model for creating knowledge."""
    title: str = Field(..., max_length=100, description="Brief title")
    content: str = Field(..., min_length=1, description="Knowledge content")
    content_type: ContentType = Field(..., description="Type of knowledge")
    category: Category = Field(..., description="Knowledge category")
    tags: list[str] = Field(default_factory=list, max_length=10)
    access_level: AccessLevel = Field(default=AccessLevel.PUBLIC)
    related_files: list[str] = Field(default_factory=list)
    expires_in_days: Optional[int] = Field(None, ge=1, le=3650)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class UpdateKnowledgeRequest(BaseModel):
    """Request model for updating knowledge."""
    title: Optional[str] = Field(None, max_length=100)
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    access_level: Optional[AccessLevel] = None
    status: Optional[KnowledgeStatus] = None
    confidence_score: Optional[float] = Field(None, ge=0.0, le=1.0)


class VoteRequest(BaseModel):
    """Request model for voting on knowledge."""
    vote: str = Field(
        ...,
        description="Vote type: helpful, unhelpful, outdated, incorrect"
    )
    comment: Optional[str] = Field(None, max_length=500)
    used_successfully: Optional[bool] = None


class SearchRequest(BaseModel):
    """Request model for semantic search."""
    query: str = Field(..., min_length=1, description="Search query")
    k: int = Field(default=10, ge=1, le=100, description="Number of results")
    category: Optional[Category] = None
    tags: Optional[list[str]] = None
    min_score: Optional[float] = Field(None, ge=0.0, le=1.0)


class KnowledgeListResponse(BaseModel):
    """Response model for knowledge listing."""
    items: list[DakbKnowledge]
    total: int
    page: int
    page_size: int


class SearchResultItem(BaseModel):
    """Individual search result."""
    knowledge: DakbKnowledge
    similarity_score: float


class SearchResponse(BaseModel):
    """Response model for semantic search."""
    results: list[SearchResultItem]
    total: int
    query: str
    search_time_ms: float


# =============================================================================
# REQUEST/RESPONSE MODELS (Knowledge Management - Step 2.2)
# =============================================================================

class BulkKnowledgeEntry(BaseModel):
    """Individual entry for bulk create."""
    title: str = Field(..., max_length=100, description="Brief title")
    content: str = Field(..., min_length=1, description="Knowledge content")
    content_type: ContentType = Field(..., description="Type of knowledge")
    category: Category = Field(..., description="Knowledge category")
    tags: list[str] = Field(default_factory=list, max_length=10)
    access_level: AccessLevel = Field(default=AccessLevel.PUBLIC)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class BulkCreateRequest(BaseModel):
    """Request model for bulk knowledge creation."""
    entries: list[BulkKnowledgeEntry] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="List of knowledge entries to create"
    )


class BulkCreateResponse(BaseModel):
    """Response model for bulk knowledge creation."""
    created_ids: list[str] = Field(
        default_factory=list,
        description="IDs of successfully created entries"
    )
    failed: list[dict] = Field(
        default_factory=list,
        description="Failed entries with error details"
    )
    success_count: int = Field(default=0)
    fail_count: int = Field(default=0)


class ListByTagsRequest(BaseModel):
    """Request model for listing by tags."""
    tags: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Tags to search for"
    )
    match_all: bool = Field(
        default=False,
        description="If true, require all tags to match"
    )
    limit: int = Field(default=50, ge=1, le=100)


class ListByTagsResponse(BaseModel):
    """Response model for list by tags."""
    items: list[DakbKnowledge]
    total: int


class RelatedKnowledgeItem(BaseModel):
    """Item in find related response."""
    knowledge: DakbKnowledge
    similarity_score: float


class FindRelatedResponse(BaseModel):
    """Response model for find related."""
    source_id: str
    related: list[RelatedKnowledgeItem]
    total: int


class StatsResponse(BaseModel):
    """Response model for knowledge base statistics."""
    total_entries: int
    by_category: dict[str, int]
    by_content_type: dict[str, int]
    by_access_level: dict[str, int]
    top_tags: list[dict]  # [{tag: str, count: int}]
    indexed_count: int
    expired_count: int


class CleanupResponse(BaseModel):
    """Response model for cleanup expired."""
    expired_count: int
    dry_run: bool
    deleted_ids: list[str] = Field(default_factory=list)
    expired_ids: list[str] = Field(default_factory=list)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

async def call_embedding_service(
    endpoint: str,
    payload: dict[str, Any],
    timeout: float = 30.0
) -> dict[str, Any]:
    """
    Call the internal embedding service.

    Args:
        endpoint: Endpoint path (e.g., "/add", "/search").
        payload: Request payload.
        timeout: Request timeout in seconds.

    Returns:
        Response JSON.

    Raises:
        HTTPException: If embedding service call fails.
    """
    settings = get_settings()

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{settings.embedding_service_url}{endpoint}",
                json=payload,
                headers={"X-Internal-Secret": settings.internal_secret},
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            logger.error(f"Embedding service timeout: {endpoint}")
            raise HTTPException(
                status_code=504,
                detail="Embedding service timeout"
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"Embedding service error: {e.response.status_code}")
            raise HTTPException(
                status_code=502,
                detail=f"Embedding service error: {e.response.status_code}"
            )
        except httpx.RequestError as e:
            logger.error(f"Embedding service connection error: {e}")
            raise HTTPException(
                status_code=503,
                detail="Embedding service unavailable"
            )


def get_repositories():
    """Get DAKB repository instances."""
    client = get_dakb_client()
    settings = get_settings()
    return get_dakb_repositories(client, settings.db_name)


# =============================================================================
# ROUTES
# =============================================================================

@router.post(
    "",
    response_model=DakbKnowledge,
    status_code=201,
    summary="Create knowledge entry",
    description="Create a new knowledge entry with automatic embedding indexing."
)
async def create_knowledge(
    request: CreateKnowledgeRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> DakbKnowledge:
    """
    Create a new knowledge entry.

    The knowledge content is automatically embedded and indexed for
    semantic search. Access control is applied based on the specified
    access level.

    Args:
        request: Knowledge creation data.
        agent: Authenticated agent (from JWT).

    Returns:
        Created knowledge entry.
    """
    repos = get_repositories()

    # Create source information
    source = KnowledgeSource(
        agent_id=agent.agent_id,
        agent_type=AgentType(agent.agent_type),
        machine_id=agent.machine_id,
        session_id=None,  # Can be added later
        context=None
    )

    # Create knowledge entry
    knowledge_data = KnowledgeCreate(
        title=request.title,
        content=request.content,
        content_type=request.content_type,
        category=request.category,
        tags=request.tags,
        access_level=request.access_level,
        related_files=request.related_files,
        expires_in_days=request.expires_in_days,
        confidence=request.confidence
    )

    # ISS-015 Fix: Knowledge is created with embedding_indexed=False (default).
    # It only becomes True after successful indexing.
    # This ensures background reconciliation can pick up failed indexes.
    knowledge = repos["knowledge"].create(knowledge_data, source)

    # Index in FAISS via embedding service
    try:
        await call_embedding_service(
            "/add",
            {
                "knowledge_id": knowledge.knowledge_id,
                "text": f"{knowledge.title}\n\n{knowledge.content}"
            }
        )
        # Mark as indexed ONLY after successful indexing
        repos["knowledge"].mark_indexed(knowledge.knowledge_id, True)
        knowledge.embedding_indexed = True
        logger.debug(f"Knowledge {knowledge.knowledge_id} indexed successfully")
    except HTTPException as e:
        logger.warning(
            f"Failed to index knowledge {knowledge.knowledge_id}: {e.detail}. "
            "Knowledge remains with embedding_indexed=False for background reconciliation."
        )
        # ISS-015: Knowledge is created but not indexed (embedding_indexed=False)
        # Background reconciliation job should pick up entries with embedding_indexed=False

    # Audit log
    repos["audit"].log(
        agent_id=agent.agent_id,
        action=AuditAction.KNOWLEDGE_CREATE,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=knowledge.knowledge_id,
        details={"title": knowledge.title, "category": knowledge.category.value},
        machine_id=agent.machine_id
    )

    # Update agent stats
    repos["agents"].increment_stats(agent.agent_id, knowledge_contributed=1)

    logger.info(f"Knowledge created: {knowledge.knowledge_id} by {agent.agent_id}")

    return knowledge


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Semantic search",
    description="Search knowledge base using semantic similarity."
)
async def search_knowledge(
    query: str = Query(..., min_length=1, description="Search query"),
    k: int = Query(default=10, ge=1, le=100, description="Number of results"),
    category: Optional[Category] = Query(None, description="Filter by category"),
    min_score: Optional[float] = Query(None, ge=0.0, le=1.0),
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> SearchResponse:
    """
    Perform semantic search on knowledge base.

    Uses FAISS for fast approximate nearest neighbor search.
    Results are filtered based on agent's access permissions.

    Args:
        query: Search query text.
        k: Number of results to return.
        category: Optional category filter.
        min_score: Minimum similarity score.
        agent: Authenticated agent.

    Returns:
        Search results with similarity scores.
    """
    repos = get_repositories()
    settings = get_settings()
    start_time = time.time()

    # Set minimum score
    effective_min_score = min_score or settings.min_similarity_score

    # Request more results to account for access filtering
    search_k = min(k * 2, settings.max_search_limit)

    # Semantic search via embedding service
    try:
        search_response = await call_embedding_service(
            "/search",
            {"query": query, "k": search_k}
        )
    except HTTPException:
        # Fallback: return empty results if embedding service unavailable
        return SearchResponse(
            results=[],
            total=0,
            query=query,
            search_time_ms=0
        )

    # Get knowledge IDs from search results
    search_results = search_response.get("results", [])
    knowledge_ids = [r["knowledge_id"] for r in search_results]
    score_map = {r["knowledge_id"]: r["score"] for r in search_results}

    if not knowledge_ids:
        return SearchResponse(
            results=[],
            total=0,
            query=query,
            search_time_ms=(time.time() - start_time) * 1000
        )

    # Fetch knowledge entries
    knowledge_entries = repos["knowledge"].get_by_ids(knowledge_ids)

    # Filter by access control and other criteria
    filtered_results = []
    for entry in knowledge_entries:
        # Access control check
        if not AccessChecker.can_access(
            agent,
            entry.access_level,
            allowed_agents=entry.allowed_agents,
            allowed_roles=entry.allowed_roles
        ):
            continue

        # Category filter
        if category and entry.category != category:
            continue

        # Skip deleted/deprecated
        if entry.status in [KnowledgeStatus.DELETED, KnowledgeStatus.DEPRECATED]:
            continue

        score = score_map.get(entry.knowledge_id, 0.0)

        # Minimum score filter
        if score < effective_min_score:
            continue

        filtered_results.append(
            SearchResultItem(knowledge=entry, similarity_score=score)
        )

    # Sort by score and limit
    filtered_results.sort(key=lambda x: x.similarity_score, reverse=True)
    filtered_results = filtered_results[:k]

    search_time_ms = (time.time() - start_time) * 1000

    return SearchResponse(
        results=filtered_results,
        total=len(filtered_results),
        query=query,
        search_time_ms=search_time_ms
    )


# =============================================================================
# STATIC ROUTES (must be defined BEFORE parametric routes)
# ISS-040 Fix: Moved static routes before /{knowledge_id} to prevent
# FastAPI from matching "/stats" as knowledge_id="stats"
# =============================================================================

@router.post(
    "/bulk",
    response_model=BulkCreateResponse,
    status_code=201,
    summary="Bulk create knowledge entries",
    description="Create multiple knowledge entries in a single request."
)
async def bulk_create_knowledge(
    request: BulkCreateRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> BulkCreateResponse:
    """
    Create multiple knowledge entries in bulk.

    Handles partial failures gracefully - some entries may succeed while
    others fail. Returns details of both successful and failed entries.

    Args:
        request: Bulk creation request with list of entries.
        agent: Authenticated agent.

    Returns:
        BulkCreateResponse with created_ids and failed entries.
    """
    repos = get_repositories()

    created_ids: list[str] = []
    failed: list[dict] = []

    # Create source information
    source = KnowledgeSource(
        agent_id=agent.agent_id,
        agent_type=AgentType(agent.agent_type),
        machine_id=agent.machine_id,
        session_id=None,
        context=None
    )

    for idx, entry in enumerate(request.entries):
        try:
            knowledge_data = KnowledgeCreate(
                title=entry.title,
                content=entry.content,
                content_type=entry.content_type,
                category=entry.category,
                tags=entry.tags,
                access_level=entry.access_level,
                related_files=[],
                expires_in_days=None,
                confidence=entry.confidence
            )

            knowledge = repos["knowledge"].create(knowledge_data, source)

            # Index in FAISS
            try:
                await call_embedding_service(
                    "/add",
                    {
                        "knowledge_id": knowledge.knowledge_id,
                        "text": f"{knowledge.title}\n\n{knowledge.content}"
                    }
                )
                repos["knowledge"].mark_indexed(knowledge.knowledge_id, True)
            except HTTPException:
                logger.warning(
                    f"Failed to index bulk entry {knowledge.knowledge_id}"
                )

            created_ids.append(knowledge.knowledge_id)

        except Exception as e:
            logger.error(f"Failed to create bulk entry {idx}: {e}")
            failed.append({
                "index": idx,
                "title": entry.title,
                "error": str(e)
            })

    # Audit log for bulk operation
    repos["audit"].log(
        agent_id=agent.agent_id,
        action=AuditAction.KNOWLEDGE_CREATE,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id="bulk_operation",
        details={
            "success_count": len(created_ids),
            "fail_count": len(failed),
            "created_ids": created_ids[:10],  # Limit audit log size
        },
        machine_id=agent.machine_id
    )

    # Update agent stats
    if created_ids:
        repos["agents"].increment_stats(
            agent.agent_id,
            knowledge_contributed=len(created_ids)
        )

    logger.info(
        f"Bulk create: {len(created_ids)} succeeded, "
        f"{len(failed)} failed by {agent.agent_id}"
    )

    return BulkCreateResponse(
        created_ids=created_ids,
        failed=failed,
        success_count=len(created_ids),
        fail_count=len(failed)
    )


@router.get(
    "/by-tags",
    response_model=ListByTagsResponse,
    summary="List knowledge by tags",
    description="List knowledge entries that match specified tags."
)
async def list_by_tags(
    tags: str = Query(..., description="Comma-separated list of tags"),
    match_all: bool = Query(
        default=False,
        description="If true, require all tags to match"
    ),
    limit: int = Query(default=50, ge=1, le=100),
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> ListByTagsResponse:
    """
    List knowledge entries that match specified tags.

    Args:
        tags: Comma-separated list of tags.
        match_all: If true, entries must have all tags.
        limit: Maximum results.
        agent: Authenticated agent.

    Returns:
        List of matching knowledge entries.
    """
    repos = get_repositories()

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        raise HTTPException(
            status_code=400,
            detail="At least one tag is required"
        )

    items = repos["knowledge"].find_by_tags(
        tag_list,
        match_all=match_all,
        limit=limit
    )

    # Filter by access control
    accessible_items = []
    for item in items:
        if AccessChecker.can_access(
            agent,
            item.access_level,
            allowed_agents=item.allowed_agents,
            allowed_roles=item.allowed_roles
        ):
            accessible_items.append(item)

    return ListByTagsResponse(
        items=accessible_items,
        total=len(accessible_items)
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Get knowledge base statistics",
    description="Get detailed statistics about the knowledge base."
)
async def get_stats(
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> StatsResponse:
    """
    Get detailed knowledge base statistics.

    Returns counts by category, content type, access level,
    and lists top tags.

    Args:
        agent: Authenticated agent.

    Returns:
        StatsResponse with detailed breakdown.
    """
    repos = get_repositories()

    # Get aggregated statistics from repository
    stats = repos["knowledge"].get_statistics()

    return StatsResponse(
        total_entries=stats.get("total_entries", 0),
        by_category=stats.get("by_category", {}),
        by_content_type=stats.get("by_content_type", {}),
        by_access_level=stats.get("by_access_level", {}),
        top_tags=stats.get("top_tags", []),
        indexed_count=stats.get("indexed_count", 0),
        expired_count=stats.get("expired_count", 0)
    )


@router.post(
    "/cleanup-expired",
    response_model=CleanupResponse,
    summary="Cleanup expired knowledge",
    description="Remove expired knowledge entries. Admin only."
)
async def cleanup_expired(
    dry_run: bool = Query(
        default=True,
        description="Preview without deleting if true"
    ),
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> CleanupResponse:
    """
    Cleanup expired knowledge entries.

    Admin-only operation. Use dry_run=true to preview what would
    be deleted without actually deleting.

    Args:
        dry_run: If true, only preview expired entries.
        agent: Authenticated agent (must be admin).

    Returns:
        CleanupResponse with expired/deleted counts and IDs.

    Raises:
        HTTPException: If agent is not admin.
    """
    # Require admin role
    if agent.role.value != "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin role required for cleanup operations"
        )

    repos = get_repositories()

    # Find expired entries - find_expired() returns list[str] (IDs only)
    # ISS-038 Fix: Correctly handle the return type
    expired_ids = repos["knowledge"].find_expired()

    if not dry_run and expired_ids:
        # Actually delete the expired entries
        deleted_ids = []
        for entry_id in expired_ids:
            try:
                # Soft delete in MongoDB
                repos["knowledge"].delete(entry_id, soft=True)

                # Remove from FAISS index
                try:
                    await call_embedding_service(
                        "/delete",
                        {"knowledge_id": entry_id}
                    )
                except HTTPException:
                    logger.warning(
                        f"Failed to remove {entry_id} from index during cleanup"
                    )

                deleted_ids.append(entry_id)
            except Exception as e:
                logger.error(f"Failed to delete expired {entry_id}: {e}")

        # Audit log
        repos["audit"].log(
            agent_id=agent.agent_id,
            action=AuditAction.KNOWLEDGE_DELETE,
            resource_type=ResourceType.KNOWLEDGE,
            resource_id="cleanup_expired",
            details={
                "deleted_count": len(deleted_ids),
                "deleted_ids": deleted_ids[:10],  # Limit audit log size
            },
            machine_id=agent.machine_id
        )

        logger.info(
            f"Cleanup expired: {len(deleted_ids)} entries deleted "
            f"by {agent.agent_id}"
        )

        return CleanupResponse(
            expired_count=len(expired_ids),
            dry_run=False,
            deleted_ids=deleted_ids,
            expired_ids=[]
        )
    else:
        # Dry run - just return preview
        return CleanupResponse(
            expired_count=len(expired_ids),
            dry_run=True,
            deleted_ids=[],
            expired_ids=expired_ids
        )


# =============================================================================
# PARAMETRIC ROUTES (must be defined AFTER static routes)
# =============================================================================

@router.get(
    "/{knowledge_id}",
    response_model=DakbKnowledge,
    summary="Get knowledge by ID",
    description="Retrieve a knowledge entry by its unique identifier."
)
async def get_knowledge(
    knowledge_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> DakbKnowledge:
    """
    Get a knowledge entry by ID.

    Access control is enforced based on the knowledge's access level
    and the agent's permissions.

    Args:
        knowledge_id: Unique knowledge identifier.
        agent: Authenticated agent.

    Returns:
        Knowledge entry.

    Raises:
        HTTPException: If not found or access denied.
    """
    repos = get_repositories()

    knowledge = repos["knowledge"].get_by_id(knowledge_id)
    if not knowledge:
        raise HTTPException(status_code=404, detail="Knowledge not found")

    # Check access control
    AccessChecker.require_access(
        agent,
        knowledge.access_level,
        knowledge_id,
        allowed_agents=knowledge.allowed_agents,
        allowed_roles=knowledge.allowed_roles
    )

    # Record access
    repos["knowledge"].record_access(knowledge_id, agent.agent_id)

    # Audit log
    repos["audit"].log(
        agent_id=agent.agent_id,
        action=AuditAction.KNOWLEDGE_READ,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=knowledge_id,
        machine_id=agent.machine_id
    )

    return knowledge


@router.put(
    "/{knowledge_id}",
    response_model=DakbKnowledge,
    summary="Update knowledge",
    description="Update an existing knowledge entry."
)
async def update_knowledge(
    knowledge_id: str,
    request: UpdateKnowledgeRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> DakbKnowledge:
    """
    Update a knowledge entry.

    Only the owner (creator) or admin can update knowledge entries.
    If content is updated, the entry is re-embedded.

    Args:
        knowledge_id: Knowledge identifier.
        request: Update data.
        agent: Authenticated agent.

    Returns:
        Updated knowledge entry.
    """
    repos = get_repositories()

    # Get existing knowledge
    existing = repos["knowledge"].get_by_id(knowledge_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Knowledge not found")

    # Check ownership or admin role
    is_owner = existing.source.agent_id == agent.agent_id
    is_admin = agent.role.value == "admin"

    if not is_owner and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only the owner or admin can update knowledge"
        )

    # Prepare update
    update_data = KnowledgeUpdate(
        title=request.title,
        content=request.content,
        tags=request.tags,
        access_level=request.access_level,
        status=request.status,
        confidence_score=request.confidence_score
    )

    # Update in MongoDB
    updated = repos["knowledge"].update(
        knowledge_id,
        update_data,
        updated_by=agent.agent_id
    )

    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update knowledge")

    # ISS-014 Fix: Re-index if content changed using atomic /reindex endpoint
    # This prevents race condition where delete succeeds but add fails,
    # leaving the knowledge entry un-indexed with no automatic recovery.
    if request.title or request.content:
        try:
            # Use atomic reindex endpoint (delete old + add new in single operation)
            await call_embedding_service(
                "/reindex",
                {
                    "knowledge_id": knowledge_id,
                    "text": f"{updated.title}\n\n{updated.content}"
                }
            )
            # Ensure indexed flag is True
            repos["knowledge"].mark_indexed(knowledge_id, True)
            logger.debug(f"Knowledge {knowledge_id} reindexed successfully")
        except HTTPException as e:
            # ISS-014: If reindex fails, mark as needing re-indexing
            repos["knowledge"].mark_indexed(knowledge_id, False)
            logger.warning(
                f"Failed to re-index {knowledge_id}: {e.detail}. "
                "Marked embedding_indexed=False for background reconciliation."
            )

    # Audit log
    repos["audit"].log(
        agent_id=agent.agent_id,
        action=AuditAction.KNOWLEDGE_UPDATE,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=knowledge_id,
        details={"fields_updated": request.model_dump(exclude_unset=True)},
        machine_id=agent.machine_id
    )

    logger.info(f"Knowledge updated: {knowledge_id} by {agent.agent_id}")

    return updated


@router.delete(
    "/{knowledge_id}",
    status_code=204,
    summary="Delete knowledge",
    description="Soft delete a knowledge entry (marks as deleted)."
)
async def delete_knowledge(
    knowledge_id: str,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> None:
    """
    Soft delete a knowledge entry.

    Marks the entry as deleted but does not remove it from the database.
    The embedding is removed from the FAISS index.

    Args:
        knowledge_id: Knowledge identifier.
        agent: Authenticated agent.
    """
    repos = get_repositories()

    # Get existing knowledge
    existing = repos["knowledge"].get_by_id(knowledge_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Knowledge not found")

    # Check ownership or admin role
    is_owner = existing.source.agent_id == agent.agent_id
    is_admin = agent.role.value == "admin"

    if not is_owner and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only the owner or admin can delete knowledge"
        )

    # Soft delete in MongoDB
    success = repos["knowledge"].delete(knowledge_id, soft=True)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete knowledge")

    # Remove from FAISS index
    try:
        await call_embedding_service(
            "/delete",
            {"knowledge_id": knowledge_id}
        )
    except HTTPException as e:
        logger.warning(f"Failed to remove from index {knowledge_id}: {e.detail}")

    # Audit log
    repos["audit"].log(
        agent_id=agent.agent_id,
        action=AuditAction.KNOWLEDGE_DELETE,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=knowledge_id,
        details={"soft_delete": True},
        machine_id=agent.machine_id
    )

    logger.info(f"Knowledge deleted: {knowledge_id} by {agent.agent_id}")


@router.post(
    "/{knowledge_id}/vote",
    response_model=DakbKnowledge,
    summary="Vote on knowledge",
    description="Cast a vote on a knowledge entry (helpful, unhelpful, outdated, incorrect)."
)
async def vote_on_knowledge(
    knowledge_id: str,
    request: VoteRequest,
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> DakbKnowledge:
    """
    Vote on a knowledge entry.

    Agents can vote on knowledge quality to help surface the most
    useful entries and identify outdated or incorrect information.

    Args:
        knowledge_id: Knowledge identifier.
        request: Vote data.
        agent: Authenticated agent.

    Returns:
        Updated knowledge entry with new vote counts.
    """
    repos = get_repositories()

    # Validate vote type
    valid_votes = ["helpful", "unhelpful", "outdated", "incorrect"]
    if request.vote not in valid_votes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid vote type. Must be one of: {valid_votes}"
        )

    # Get existing knowledge
    existing = repos["knowledge"].get_by_id(knowledge_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Knowledge not found")

    # Check access - must be able to read to vote
    AccessChecker.require_access(
        agent,
        existing.access_level,
        knowledge_id,
        allowed_agents=existing.allowed_agents,
        allowed_roles=existing.allowed_roles
    )

    # Create vote
    from ...db.schemas import VoteType
    vote_data = VoteCreate(
        vote=VoteType(request.vote),
        comment=request.comment,
        used_successfully=request.used_successfully
    )

    # Record vote
    updated = repos["knowledge"].vote(knowledge_id, agent.agent_id, vote_data)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to record vote")

    # Audit log
    repos["audit"].log(
        agent_id=agent.agent_id,
        action=AuditAction.KNOWLEDGE_VOTE,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=knowledge_id,
        details={"vote": request.vote, "comment": request.comment},
        machine_id=agent.machine_id
    )

    logger.info(f"Vote recorded on {knowledge_id}: {request.vote} by {agent.agent_id}")

    return updated


@router.get(
    "",
    response_model=KnowledgeListResponse,
    summary="List knowledge",
    description="List knowledge entries with optional filtering."
)
async def list_knowledge(
    category: Optional[Category] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tags"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> KnowledgeListResponse:
    """
    List knowledge entries with filtering and pagination.

    Args:
        category: Filter by category.
        tags: Comma-separated list of tags to filter by.
        page: Page number (1-indexed).
        page_size: Items per page.
        agent: Authenticated agent.

    Returns:
        Paginated list of knowledge entries.
    """
    repos = get_repositories()

    skip = (page - 1) * page_size

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        items = repos["knowledge"].find_by_tags(tag_list, limit=page_size)
    elif category:
        items = repos["knowledge"].find_by_category(
            category.value,
            limit=page_size,
            skip=skip
        )
    else:
        # Require at least one filter for now
        raise HTTPException(
            status_code=400,
            detail="At least one filter (category or tag) is required"
        )

    # Filter by access control
    accessible_items = []
    for item in items:
        if AccessChecker.can_access(
            agent,
            item.access_level,
            allowed_agents=item.allowed_agents,
            allowed_roles=item.allowed_roles
        ):
            accessible_items.append(item)

    return KnowledgeListResponse(
        items=accessible_items,
        total=len(accessible_items),
        page=page,
        page_size=page_size
    )


@router.get(
    "/{knowledge_id}/related",
    response_model=FindRelatedResponse,
    summary="Find related knowledge",
    description="Find knowledge entries semantically related to a given entry."
)
async def find_related(
    knowledge_id: str,
    limit: int = Query(default=5, ge=1, le=20),
    agent: AuthenticatedAgent = Depends(get_current_agent)
) -> FindRelatedResponse:
    """
    Find knowledge entries related to a given entry.

    Uses semantic similarity to find related knowledge. The source
    entry's content is used as the search query.

    Args:
        knowledge_id: ID of the source knowledge entry.
        limit: Maximum related entries to return.
        agent: Authenticated agent.

    Returns:
        FindRelatedResponse with related entries and similarity scores.
    """
    repos = get_repositories()
    settings = get_settings()

    # Get source knowledge
    source = repos["knowledge"].get_by_id(knowledge_id)
    if not source:
        raise HTTPException(status_code=404, detail="Knowledge not found")

    # Check access to source
    AccessChecker.require_access(
        agent,
        source.access_level,
        knowledge_id,
        allowed_agents=source.allowed_agents,
        allowed_roles=source.allowed_roles
    )

    # Search using source content as query
    search_text = f"{source.title}\n\n{source.content[:500]}"

    try:
        search_response = await call_embedding_service(
            "/search",
            {"query": search_text, "k": limit + 1}  # +1 to exclude self
        )
    except HTTPException:
        return FindRelatedResponse(
            source_id=knowledge_id,
            related=[],
            total=0
        )

    # Get knowledge IDs from search results
    search_results = search_response.get("results", [])
    knowledge_ids = [
        r["knowledge_id"]
        for r in search_results
        if r["knowledge_id"] != knowledge_id  # Exclude source
    ]
    score_map = {r["knowledge_id"]: r["score"] for r in search_results}

    if not knowledge_ids:
        return FindRelatedResponse(
            source_id=knowledge_id,
            related=[],
            total=0
        )

    # Fetch knowledge entries
    knowledge_entries = repos["knowledge"].get_by_ids(knowledge_ids[:limit])

    # Filter by access and build response
    related_items = []
    for entry in knowledge_entries:
        if not AccessChecker.can_access(
            agent,
            entry.access_level,
            allowed_agents=entry.allowed_agents,
            allowed_roles=entry.allowed_roles
        ):
            continue

        if entry.status in [KnowledgeStatus.DELETED, KnowledgeStatus.DEPRECATED]:
            continue

        score = score_map.get(entry.knowledge_id, 0.0)
        if score >= settings.min_similarity_score:
            related_items.append(
                RelatedKnowledgeItem(knowledge=entry, similarity_score=score)
            )

    # Sort by score
    related_items.sort(key=lambda x: x.similarity_score, reverse=True)

    return FindRelatedResponse(
        source_id=knowledge_id,
        related=related_items[:limit],
        total=len(related_items)
    )
