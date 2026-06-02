# DAKB Admin Dashboard Sync - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Sync DAKB Open with dev edition admin dashboard features from January 20-22, 2026 updates.

**Architecture:** The admin module already exists in DAKB Open with core functionality. This plan adds missing features: Knowledge Base Management UI, extended role support, token management endpoints, and MCP session configuration improvements.

**Tech Stack:** FastAPI, Pydantic v2, MongoDB, Jinja2 templates, Bootstrap 5

**Reference:** `/Users/oracleseed/Documents/Biz/Trade Analyst/tradebot/.claude/reports/DAKB_ADMIN_UPDATES_20260120.md`

---

## Analysis Summary

### Current State (DAKB Open)

| File | Current Value | Issue |
|------|---------------|-------|
| `dakb/admin/api.py:1271` | 4 roles: admin, developer, researcher, viewer | Missing 4 roles |
| `dakb/gateway/mcp_session.py:58` | `session_timeout_seconds = 3600` (1 hour) | Too short |
| `dakb/gateway/mcp_session.py:59` | `max_sessions_per_agent = 10` | Too low |
| Token endpoints | Missing `/all-agents/{id}/token-info` etc. | Need 3 endpoints |
| Knowledge CRUD | Missing 6 endpoints | Full implementation needed |

### Features from Dev Edition (Jan 20-22, 2026)

| Feature | Dev Edition | DAKB Open |
|---------|-------------|-----------|
| 8 roles | developer, admin, researcher, viewer, specialist, coordinator, analyst, observer | Only 4 |
| `/all-agents/{id}/token-info` | Implemented | **Missing** |
| `/all-agents/{id}/regenerate-token` | Implemented | **Missing** |
| `/all-agents/{id}/expire-token` | Implemented | **Missing** |
| `/admin/knowledge` CRUD (6 endpoints) | Implemented | **Missing** |
| MCP session timeout | 86400 (24h) | 3600 (1h) |
| Max sessions/agent | 50 | 10 |
| Invite token fields | `granted_role`, `granted_access_levels` | Verify |

---

## Task 1: Update MCP Session Configuration Defaults

**Files:**
- Modify: `dakb/gateway/mcp_session.py:58-59`

**Step 1: Update session_timeout_seconds default**

```python
# dakb/gateway/mcp_session.py line 58
# BEFORE:
session_timeout_seconds: int = _get_env_int("DAKB_MCP_SESSION_TIMEOUT", 3600)

# AFTER:
session_timeout_seconds: int = _get_env_int("DAKB_MCP_SESSION_TIMEOUT", 86400)  # 24 hours
```

**Step 2: Update max_sessions_per_agent default**

```python
# dakb/gateway/mcp_session.py line 59
# BEFORE:
max_sessions_per_agent: int = _get_env_int("DAKB_MCP_MAX_SESSIONS_PER_AGENT", 10)

# AFTER:
max_sessions_per_agent: int = _get_env_int("DAKB_MCP_MAX_SESSIONS_PER_AGENT", 50)  # Increased from 10
```

**Step 3: Update docstring comment**

```python
# Line 21-22 update:
# - DAKB_MCP_SESSION_TIMEOUT: Session idle timeout in seconds (default: 86400 = 24 hours)
# - DAKB_MCP_MAX_SESSIONS_PER_AGENT: Max sessions per agent (default: 50)
```

**Step 4: Commit**

```bash
git add dakb/gateway/mcp_session.py
git commit -m "feat(mcp): increase session timeout to 24h, max sessions to 50"
```

---

## Task 2: Extend Valid Roles to 8 Roles

**Files:**
- Modify: `dakb/admin/api.py:1271-1276`

**Step 1: Update valid_roles list**

```python
# dakb/admin/api.py - in update_agent function, around line 1271
# BEFORE:
valid_roles = ["admin", "developer", "researcher", "viewer"]

# AFTER:
valid_roles = [
    "admin", "developer", "researcher", "viewer",
    "specialist", "coordinator", "analyst", "observer"
]
```

**Step 2: Verify test passes**

Run: `pytest tests/ -k role -v`
Expected: PASS

**Step 3: Commit**

```bash
git add dakb/admin/api.py
git commit -m "feat(admin): extend valid_roles to 8 (specialist, coordinator, analyst, observer)"
```

---

## Task 3: Add Token Management Endpoints for All Agents

**Files:**
- Modify: `dakb/admin/api.py` (add after line 1505)

**Step 1: Add required imports at top of file**

```python
# Add to imports section if not present:
import secrets
from datetime import timedelta
from ..db.admin_schemas import hash_token
```

**Step 2: Add TokenInfoResponse model**

```python
# Add after existing response models (around line 1090)

class TokenInfoResponse(BaseModel):
    """Response model for token info endpoint."""
    agent_id: str
    token_status: str  # active, expired, revoked, unknown
    expires_at: datetime | None = None
    days_until_expiry: int = 0
    role: str | None = None
    access_levels: list[str] = Field(default_factory=list)
    last_used_at: datetime | None = None
    use_count: int = 0
```

**Step 3: Add TokenRegenerateRequest model**

```python
class TokenRegenerateRequest(BaseModel):
    """Request model for token regeneration."""
    expires_in_hours: int = Field(default=8760, ge=1, le=87600, description="Hours until expiry (default 1 year)")
    role: str | None = Field(None, description="New role (optional, keeps existing)")
    access_levels: list[str] | None = Field(None, description="New access levels (optional)")
```

**Step 4: Add TokenRegenerateResponse model**

```python
class TokenRegenerateResponse(BaseModel):
    """Response model for token regeneration."""
    success: bool
    agent_id: str
    token: str  # Only shown once!
    expires_at: datetime
    message: str = "Save this token securely - it will not be shown again"
```

**Step 5: Implement /all-agents/{agent_id}/token-info endpoint**

```python
# Add after delete_agent endpoint (around line 1505)

@router.get(
    "/all-agents/{agent_id}/token-info",
    response_model=TokenInfoResponse,
    summary="Get agent token info",
    description="Get token status and expiration info for an agent. Requires admin privileges.",
)
async def get_agent_token_info(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> TokenInfoResponse:
    """Get token information for any registered agent."""
    db = get_db()

    # Check agent exists
    agent_doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})
    if not agent_doc:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    # Get token info from registry
    token_doc = db[COLLECTION_TOKEN_REGISTRY].find_one({"agent_id": agent_id})

    if not token_doc:
        return TokenInfoResponse(
            agent_id=agent_id,
            token_status="unknown",
            days_until_expiry=0,
        )

    token = TokenRegistryDocument(**token_doc)

    # Calculate status
    now = utcnow()
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if token.status == TokenStatus.REVOKED:
        status = "revoked"
        days_until_expiry = 0
    elif expires_at <= now:
        status = "expired"
        days_until_expiry = 0
    else:
        status = "active"
        days_until_expiry = (expires_at - now).days

    return TokenInfoResponse(
        agent_id=agent_id,
        token_status=status,
        expires_at=token.expires_at,
        days_until_expiry=days_until_expiry,
        role=token.role,
        access_levels=token.access_levels,
        last_used_at=token.last_used_at,
        use_count=token.use_count,
    )
```

**Step 6: Implement /all-agents/{agent_id}/regenerate-token endpoint**

```python
@router.post(
    "/all-agents/{agent_id}/regenerate-token",
    response_model=TokenRegenerateResponse,
    summary="Regenerate agent token",
    description="Generate a new authentication token for an agent. Returns token only once. Requires admin privileges.",
)
async def regenerate_agent_token(
    agent_id: str,
    request: TokenRegenerateRequest = TokenRegenerateRequest(),
    admin: AuthenticatedAgent = Depends(require_admin)
) -> TokenRegenerateResponse:
    """Regenerate token for an agent with new expiry."""
    db = get_db()

    # Check agent exists
    agent_doc = db[COLLECTION_AGENTS].find_one({"agent_id": agent_id})
    if not agent_doc:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    # Generate new token
    new_token = secrets.token_urlsafe(32)
    new_fingerprint = hash_token(new_token)
    new_expires = utcnow() + timedelta(hours=request.expires_in_hours)

    # Prepare update
    update_data = {
        "token_fingerprint": new_fingerprint,
        "expires_at": new_expires,
        "status": TokenStatus.ACTIVE,
        "updated_at": utcnow(),
    }

    # Optionally update role and access_levels
    if request.role is not None:
        update_data["role"] = request.role
    if request.access_levels is not None:
        update_data["access_levels"] = request.access_levels

    # Update or create token registry entry
    db[COLLECTION_TOKEN_REGISTRY].update_one(
        {"agent_id": agent_id},
        {"$set": update_data},
        upsert=True
    )

    logger.info(f"Token regenerated for {agent_id} by {admin.agent_id}, expires: {new_expires}")

    return TokenRegenerateResponse(
        success=True,
        agent_id=agent_id,
        token=new_token,
        expires_at=new_expires,
    )
```

**Step 7: Implement /all-agents/{agent_id}/expire-token endpoint**

```python
@router.post(
    "/all-agents/{agent_id}/expire-token",
    response_model=AgentActionResponse,
    summary="Expire agent token",
    description="Immediately expire an agent's token (soft revoke). Requires admin privileges.",
)
async def expire_agent_token(
    agent_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> AgentActionResponse:
    """Expire token immediately."""
    db = get_db()

    # Check token exists
    token_doc = db[COLLECTION_TOKEN_REGISTRY].find_one({"agent_id": agent_id})
    if not token_doc:
        raise HTTPException(status_code=404, detail=f"No token for agent '{agent_id}'")

    # Prevent self-expiration
    if agent_id == admin.agent_id:
        raise HTTPException(status_code=400, detail="Cannot expire your own token")

    # Update token status
    db[COLLECTION_TOKEN_REGISTRY].update_one(
        {"agent_id": agent_id},
        {
            "$set": {
                "status": TokenStatus.EXPIRED,
                "expires_at": utcnow(),
                "updated_at": utcnow(),
                "revoked_by": admin.agent_id,
                "revocation_reason": "Manually expired by admin",
            }
        }
    )

    logger.info(f"Token expired for {agent_id} by {admin.agent_id}")

    return AgentActionResponse(
        success=True,
        agent_id=agent_id,
        action="expire_token",
        message=f"Token for agent '{agent_id}' has been expired"
    )
```

**Step 8: Commit**

```bash
git add dakb/admin/api.py
git commit -m "feat(admin): add token-info, regenerate-token, expire-token endpoints"
```

---

## Task 4: Add Knowledge Base Management API Endpoints

**Files:**
- Modify: `dakb/admin/api.py` (add new section)

**Step 1: Add Knowledge response models**

```python
# =============================================================================
# KNOWLEDGE BASE MANAGEMENT ENDPOINTS
# =============================================================================

COLLECTION_KNOWLEDGE = "dakb_knowledge"


class KnowledgeEntry(BaseModel):
    """Knowledge entry data model."""
    knowledge_id: str
    title: str
    content: str
    category: str
    status: str
    content_type: str
    tags: list[str] = Field(default_factory=list)
    source: dict = Field(default_factory=dict)
    votes: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class KnowledgeListResponse(BaseModel):
    """Response for knowledge list."""
    items: list[dict]
    total: int
    page: int
    page_size: int
    has_more: bool


class KnowledgeStatsResponse(BaseModel):
    """Response for knowledge statistics."""
    total_entries: int
    categories: dict
    content_types: dict
    statuses: dict
    agents: dict
    recent_24h: int


class KnowledgeUpdateRequest(BaseModel):
    """Request to update knowledge entry."""
    title: str | None = None
    content: str | None = None
    category: str | None = None
    status: str | None = None
    tags: list[str] | None = None


class KnowledgeDeleteResponse(BaseModel):
    """Response for knowledge deletion."""
    success: bool
    knowledge_id: str
    action: str  # soft_delete or hard_delete
    message: str


class RedundancyCheckResponse(BaseModel):
    """Response for redundancy check."""
    knowledge_id: str
    title: str
    similar_entries: list[dict]
    highest_similarity: float
```

**Step 2: Implement GET /admin/knowledge (list with search/filters)**

```python
@router.get(
    "/knowledge",
    response_model=KnowledgeListResponse,
    summary="List knowledge entries",
    description="List knowledge with search, filter, and pagination. Admin only.",
)
async def list_knowledge(
    page: int = 1,
    page_size: int = 20,
    category: str | None = None,
    status: str | None = None,
    agent_id: str | None = None,
    search: str | None = None,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> KnowledgeListResponse:
    """List knowledge entries with filtering and search."""
    import re
    db = get_db()

    query = {}

    # Apply filters
    if category:
        query["category"] = category
    if status:
        query["status"] = status
    if agent_id:
        query["source.agent_id"] = agent_id

    # Apply search (MongoDB regex on title, content, tags, ID)
    if search:
        search_regex = re.compile(re.escape(search), re.IGNORECASE)
        query["$or"] = [
            {"title": {"$regex": search_regex}},
            {"content": {"$regex": search_regex}},
            {"tags": {"$regex": search_regex}},
            {"knowledge_id": {"$regex": search_regex}},
        ]

    # Pagination
    skip = (page - 1) * page_size
    cursor = db[COLLECTION_KNOWLEDGE].find(query).skip(skip).limit(page_size + 1).sort("created_at", -1)

    items = list(cursor)
    has_more = len(items) > page_size
    items = items[:page_size]

    # Convert ObjectId to string
    for item in items:
        item["_id"] = str(item["_id"])

    total = db[COLLECTION_KNOWLEDGE].count_documents(query)

    return KnowledgeListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=has_more,
    )
```

**Step 3: Implement GET /admin/knowledge-stats**

```python
@router.get(
    "/knowledge-stats",
    response_model=KnowledgeStatsResponse,
    summary="Get knowledge statistics",
    description="Get knowledge base statistics. Admin only.",
)
async def get_knowledge_stats(
    admin: AuthenticatedAgent = Depends(require_admin)
) -> KnowledgeStatsResponse:
    """Get knowledge base statistics."""
    from datetime import timedelta
    db = get_db()

    total = db[COLLECTION_KNOWLEDGE].count_documents({})

    # By category
    categories = {}
    for doc in db[COLLECTION_KNOWLEDGE].aggregate([
        {"$group": {"_id": "$category", "count": {"$sum": 1}}}
    ]):
        categories[doc["_id"] or "unknown"] = doc["count"]

    # By content_type
    content_types = {}
    for doc in db[COLLECTION_KNOWLEDGE].aggregate([
        {"$group": {"_id": "$content_type", "count": {"$sum": 1}}}
    ]):
        content_types[doc["_id"] or "unknown"] = doc["count"]

    # By status
    statuses = {}
    for doc in db[COLLECTION_KNOWLEDGE].aggregate([
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]):
        statuses[doc["_id"] or "unknown"] = doc["count"]

    # By agent
    agents = {}
    for doc in db[COLLECTION_KNOWLEDGE].aggregate([
        {"$group": {"_id": "$source.agent_id", "count": {"$sum": 1}}}
    ]):
        agents[doc["_id"] or "unknown"] = doc["count"]

    # Recent (last 24h)
    cutoff = utcnow() - timedelta(hours=24)
    recent_24h = db[COLLECTION_KNOWLEDGE].count_documents({"created_at": {"$gte": cutoff}})

    return KnowledgeStatsResponse(
        total_entries=total,
        categories=categories,
        content_types=content_types,
        statuses=statuses,
        agents=agents,
        recent_24h=recent_24h,
    )
```

**Step 4: Implement GET /admin/knowledge/{knowledge_id}**

```python
@router.get(
    "/knowledge/{knowledge_id}",
    summary="Get single knowledge entry",
    description="Get full knowledge entry by ID. Admin only.",
)
async def get_knowledge_entry(
    knowledge_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> dict:
    """Get single knowledge entry."""
    db = get_db()
    doc = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})

    if not doc:
        raise HTTPException(status_code=404, detail=f"Knowledge '{knowledge_id}' not found")

    doc["_id"] = str(doc["_id"])
    return doc
```

**Step 5: Implement PUT /admin/knowledge/{knowledge_id}**

```python
@router.put(
    "/knowledge/{knowledge_id}",
    summary="Update knowledge entry",
    description="Update knowledge entry fields. Admin only.",
)
async def update_knowledge_entry(
    knowledge_id: str,
    request: KnowledgeUpdateRequest,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> dict:
    """Update knowledge entry."""
    db = get_db()
    doc = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})

    if not doc:
        raise HTTPException(status_code=404, detail=f"Knowledge '{knowledge_id}' not found")

    update_doc = {"updated_at": utcnow()}

    if request.title is not None:
        update_doc["title"] = request.title
    if request.content is not None:
        update_doc["content"] = request.content
    if request.category is not None:
        update_doc["category"] = request.category
    if request.status is not None:
        update_doc["status"] = request.status
    if request.tags is not None:
        update_doc["tags"] = request.tags

    db[COLLECTION_KNOWLEDGE].update_one(
        {"knowledge_id": knowledge_id},
        {"$set": update_doc}
    )

    logger.info(f"Knowledge updated: {knowledge_id} by {admin.agent_id}")

    updated = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})
    updated["_id"] = str(updated["_id"])
    return updated
```

**Step 6: Implement DELETE /admin/knowledge/{knowledge_id}**

```python
@router.delete(
    "/knowledge/{knowledge_id}",
    response_model=KnowledgeDeleteResponse,
    summary="Delete knowledge entry",
    description="Soft or hard delete knowledge entry. Admin only.",
)
async def delete_knowledge_entry(
    knowledge_id: str,
    hard_delete: bool = False,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> KnowledgeDeleteResponse:
    """Delete knowledge entry (soft or hard)."""
    db = get_db()
    doc = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})

    if not doc:
        raise HTTPException(status_code=404, detail=f"Knowledge '{knowledge_id}' not found")

    if hard_delete:
        db[COLLECTION_KNOWLEDGE].delete_one({"knowledge_id": knowledge_id})
        action = "hard_delete"
        message = f"Knowledge '{knowledge_id}' permanently deleted"
    else:
        db[COLLECTION_KNOWLEDGE].update_one(
            {"knowledge_id": knowledge_id},
            {
                "$set": {
                    "status": "deleted",
                    "deleted_at": utcnow(),
                    "deleted_by": admin.agent_id,
                }
            }
        )
        action = "soft_delete"
        message = f"Knowledge '{knowledge_id}' marked as deleted"

    logger.info(f"Knowledge {action}: {knowledge_id} by {admin.agent_id}")

    return KnowledgeDeleteResponse(
        success=True,
        knowledge_id=knowledge_id,
        action=action,
        message=message,
    )
```

**Step 7: Implement GET /admin/knowledge/{knowledge_id}/redundancy**

```python
@router.get(
    "/knowledge/{knowledge_id}/redundancy",
    response_model=RedundancyCheckResponse,
    summary="Check knowledge redundancy",
    description="Find similar knowledge entries. Admin only.",
)
async def check_knowledge_redundancy(
    knowledge_id: str,
    admin: AuthenticatedAgent = Depends(require_admin)
) -> RedundancyCheckResponse:
    """Check for similar/redundant knowledge entries."""
    import re
    db = get_db()

    doc = db[COLLECTION_KNOWLEDGE].find_one({"knowledge_id": knowledge_id})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Knowledge '{knowledge_id}' not found")

    title = doc.get("title", "")
    similar = []
    highest_similarity = 0.0

    # Simple text-based similarity check using title prefix
    if title and len(title) >= 10:
        search_prefix = title[:20]
        search_regex = re.compile(re.escape(search_prefix), re.IGNORECASE)

        cursor = db[COLLECTION_KNOWLEDGE].find({
            "knowledge_id": {"$ne": knowledge_id},
            "title": {"$regex": search_regex}
        }).limit(10)

        for match in cursor:
            # Simple similarity score based on title match
            similarity = 0.5 + (0.3 if match.get("category") == doc.get("category") else 0)
            similar.append({
                "knowledge_id": match["knowledge_id"],
                "title": match.get("title"),
                "category": match.get("category"),
                "similarity": similarity,
            })
            highest_similarity = max(highest_similarity, similarity)

    return RedundancyCheckResponse(
        knowledge_id=knowledge_id,
        title=title,
        similar_entries=similar,
        highest_similarity=highest_similarity,
    )
```

**Step 8: Commit**

```bash
git add dakb/admin/api.py
git commit -m "feat(admin): add knowledge base CRUD endpoints (list, stats, get, update, delete, redundancy)"
```

---

## Task 5: Update Dashboard UI for New Features

**Files:**
- Modify: `dakb/admin/templates/dashboard.html`

**Step 1: Add Knowledge Base navigation link in sidebar**

**Step 2: Add 8-role dropdown options to all role selects**

```html
<option value="developer">Developer</option>
<option value="admin">Admin</option>
<option value="researcher">Researcher</option>
<option value="viewer">Viewer (Read-only)</option>
<option value="specialist">Specialist</option>
<option value="coordinator">Coordinator</option>
<option value="analyst">Analyst</option>
<option value="observer">Observer</option>
```

**Step 3: Add Token Management button to agents table**

**Step 4: Add Token Management Modal**

**Step 5: Add Knowledge Base section with:**
- Stats cards
- Search input
- Filter dropdowns
- Entries table with pagination
- View/Edit modal
- Delete confirmation

**Step 6: Add JavaScript functions:**
- `manageAgentToken(agentId)`
- `loadTokenInfo(agentId)`
- `regenerateAgentToken()`
- `expireAgentToken()`
- `loadKnowledgeStats()`
- `loadKnowledgeEntries()`
- `viewKnowledgeEntry(id)`
- `saveKnowledgeEntry()`
- `deleteKnowledge(id, hardDelete)`
- `checkRedundancy(id)`

**Step 7: Commit**

```bash
git add dakb/admin/templates/dashboard.html
git commit -m "feat(admin-ui): add knowledge management, extended roles, token actions"
```

---

## Task 6: Verify Invite Token Field Names in Registration

**Files:**
- Check: `dakb/gateway/routes/registration.py`

**Step 1: Verify CreateInviteRequest model has correct field names**

Expected fields from dev edition:
```python
granted_role: Optional[str] = Field(
    default="developer",
    description="Role to grant the agent"
)
granted_access_levels: Optional[list[str]] = Field(
    default=["public"],
    description="Access levels to grant"
)
```

**Step 2: Fix if needed**

**Step 3: Commit if changes made**

```bash
git add dakb/gateway/routes/registration.py
git commit -m "fix(registration): update invite token field names to granted_role/granted_access_levels"
```

---

## Task 7: Run Full Test Suite and Linting

**Step 1: Run all tests**

```bash
pytest tests/ -v --tb=short
```

**Step 2: Run linting**

```bash
ruff check dakb/ --fix
ruff format dakb/
```

**Step 3: Run type checking**

```bash
mypy dakb/ --ignore-missing-imports
```

**Step 4: Final commit**

```bash
git add -A
git commit -m "chore: fix linting and type issues from admin dashboard sync"
```

---

## Summary of Changes

| Task | Files | Lines Added (Est.) |
|------|-------|-------------------|
| MCP Session Config | `mcp_session.py` | ~5 |
| Extended Roles | `api.py` | ~5 |
| Token Endpoints (3) | `api.py` | ~150 |
| Knowledge CRUD (6) | `api.py` | ~300 |
| Dashboard UI | `dashboard.html` | ~400 |
| Registration Fix | `registration.py` | ~10 |
| **Total** | | **~870 lines** |

## Environment Variables

| Variable | Old Default | New Default |
|----------|-------------|-------------|
| `DAKB_MCP_SESSION_TIMEOUT` | 3600 (1h) | 86400 (24h) |
| `DAKB_MCP_MAX_SESSIONS_PER_AGENT` | 10 | 50 |

## New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/admin/all-agents/{id}/token-info` | Token status |
| POST | `/api/v1/admin/all-agents/{id}/regenerate-token` | New token |
| POST | `/api/v1/admin/all-agents/{id}/expire-token` | Expire token |
| GET | `/api/v1/admin/knowledge` | List with search/filters |
| GET | `/api/v1/admin/knowledge-stats` | KB statistics |
| GET | `/api/v1/admin/knowledge/{id}` | Single entry |
| PUT | `/api/v1/admin/knowledge/{id}` | Update entry |
| DELETE | `/api/v1/admin/knowledge/{id}` | Soft/hard delete |
| GET | `/api/v1/admin/knowledge/{id}/redundancy` | Similarity check |

---

Plan complete and saved. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
