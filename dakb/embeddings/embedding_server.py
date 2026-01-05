"""
DAKB Embedding Server - Secured FastAPI Server

Internal-only embedding service accessible via loopback (127.0.0.1) only.
Requires X-Internal-Secret header for authentication.

Version: 1.5
Created: 2025-12-07
Updated: 2025-12-09
Author: Backend Agent (Claude Opus 4.5)

Changelog v1.5:
- Fixed MPS semaphore leak by setting multiprocessing start method to 'spawn'
- Added PYTORCH_ENABLE_MPS_FALLBACK for stability on Apple Silicon
- Added TOKENIZERS_PARALLELISM=false to prevent multiprocessing issues

Changelog v1.4:
- Replaced deprecated @app.on_event("startup") with lifespan context manager
- Compatible with FastAPI 0.95+

Security Features:
- Binds to 127.0.0.1 ONLY (loopback, not 0.0.0.0)
- Requires X-Internal-Secret header for all authenticated endpoints
- Uses constant-time comparison to prevent timing attacks
- Validates source IP is loopback
- No external network access permitted

Port: 3101 (internal only)
"""

# CRITICAL: Set multiprocessing start method BEFORE any imports
# This fixes semaphore leaks on MPS (Apple Silicon) devices
import multiprocessing
try:
    multiprocessing.set_start_method('spawn', force=True)
except RuntimeError:
    pass  # Already set

import os

# Set environment variables for MPS stability BEFORE importing torch/transformers
os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')

import secrets
import hashlib
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# =============================================================================
# PYDANTIC MODELS (v1.3 - All required models)
# =============================================================================

class EmbedRequest(BaseModel):
    """Request model for embedding generation."""
    texts: list[str] = Field(
        ...,
        description="List of texts to embed",
        min_length=1
    )


class SearchRequest(BaseModel):
    """Request model for semantic search."""
    query: str = Field(
        ...,
        description="Query text for semantic search",
        min_length=1
    )
    k: int = Field(
        default=5,
        description="Number of results to return",
        ge=1,
        le=100
    )


class AddRequest(BaseModel):
    """Request model for adding vector to index."""
    knowledge_id: str = Field(
        ...,
        description="Unique knowledge entry ID",
        min_length=1
    )
    text: str = Field(
        ...,
        description="Text content to embed and index",
        min_length=1
    )


class DeleteRequest(BaseModel):
    """Request model for soft-deleting from index."""
    knowledge_id: str = Field(
        ...,
        description="Knowledge entry ID to delete",
        min_length=1
    )


class ReindexRequest(BaseModel):
    """Request model for atomic re-indexing (delete old + add new)."""
    knowledge_id: str = Field(
        ...,
        description="Knowledge entry ID to reindex",
        min_length=1
    )
    text: str = Field(
        ...,
        description="New text content to embed and index",
        min_length=1
    )


class EmbedResponse(BaseModel):
    """Response model for embedding generation."""
    embeddings: list[list[float]] = Field(
        ...,
        description="List of embedding vectors"
    )


class SearchResult(BaseModel):
    """Individual search result."""
    knowledge_id: str = Field(..., description="Knowledge entry ID")
    score: float = Field(..., description="Similarity score")


class SearchResponse(BaseModel):
    """Response model for semantic search."""
    results: list[SearchResult] = Field(
        default_factory=list,
        description="Search results sorted by similarity"
    )


class SuccessResponse(BaseModel):
    """Generic success response."""
    success: bool = Field(default=True)


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(default="ok")
    secured: bool = Field(default=True)
    binding: str = Field(default="127.0.0.1:3101")


class StatsResponse(BaseModel):
    """Service statistics response."""
    model: str
    dimension: int
    index_type: str
    total_vectors: int
    active_vectors: int
    deleted_vectors: int


# =============================================================================
# LIFESPAN CONTEXT MANAGER (FastAPI 0.95+ replacement for @app.on_event)
# =============================================================================

# Global embedding service instance (initialized in lifespan)
embedding_service = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI application.
    Replaces deprecated @app.on_event("startup") decorator.

    Handles:
    - Startup: Initialize EmbeddingService
    - Shutdown: Cleanup and logging
    """
    global embedding_service

    # STARTUP
    logger.info("Initializing EmbeddingService...")

    # Import here to avoid circular imports and allow lazy loading
    from .embedding_service import EmbeddingService

    embedding_service = EmbeddingService()
    logger.info(
        f"EmbeddingService ready with {embedding_service.index.ntotal} vectors"
    )

    yield  # Application runs here

    # SHUTDOWN
    logger.info("Shutting down EmbeddingService...")


# =============================================================================
# FASTAPI APPLICATION
# =============================================================================

app = FastAPI(
    title="DAKB Embedding Service (Internal Only)",
    description="Secured embedding service for DAKB. Loopback access only.",
    version="1.3",
    lifespan=lifespan,  # Use lifespan context manager
    docs_url=None,  # Disable Swagger UI for security
    redoc_url=None,  # Disable ReDoc for security
    openapi_url=None  # Disable OpenAPI schema for security
)

# SECURITY: Only allow requests from localhost
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1"]
)


# =============================================================================
# SERVICE DEPENDENCY (v1.4 - Uses lifespan context manager for initialization)
# =============================================================================

def get_embedding_service():
    """
    Get the embedding service instance.

    Returns:
        EmbeddingService instance.

    Raises:
        HTTPException: If service is not initialized.
    """
    if embedding_service is None:
        raise HTTPException(
            status_code=503,
            detail="Embedding service not initialized. Wait for startup."
        )
    return embedding_service


# =============================================================================
# SECURITY CONFIGURATION
# =============================================================================

# Internal secret for gateway -> embedding service communication
INTERNAL_SECRET: Optional[str] = os.getenv("DAKB_INTERNAL_SECRET")


def get_internal_secret() -> str:
    """
    Get internal secret from environment.

    Returns:
        Internal secret string.

    Raises:
        RuntimeError: If secret is not configured.
    """
    if not INTERNAL_SECRET:
        raise RuntimeError("DAKB_INTERNAL_SECRET environment variable not set!")
    return INTERNAL_SECRET


async def verify_internal_request(
    request: Request,
    x_internal_secret: str = Header(..., alias="X-Internal-Secret")
) -> None:
    """
    Verify request comes from DAKB Gateway with valid internal secret.
    Rejects all unauthorized requests.

    Args:
        request: FastAPI request object.
        x_internal_secret: Internal secret from header.

    Raises:
        HTTPException: If authentication fails.
    """
    # Get expected secret
    try:
        expected_secret = get_internal_secret()
    except RuntimeError as e:
        logger.error(f"Security configuration error: {e}")
        raise HTTPException(
            status_code=500,
            detail="Server configuration error"
        )

    # Verify secret using constant-time comparison (prevents timing attacks)
    expected_hash = hashlib.sha256(expected_secret.encode()).hexdigest()
    provided_hash = hashlib.sha256(x_internal_secret.encode()).hexdigest()

    if not secrets.compare_digest(expected_hash, provided_hash):
        logger.warning(f"Invalid internal secret from {request.client.host}")
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Invalid internal secret"
        )

    # Additional check: verify source IP is loopback
    client_ip = request.client.host if request.client else "unknown"
    if client_ip not in ["127.0.0.1", "::1", "localhost"]:
        logger.warning(f"External access attempt from {client_ip}")
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: External access not allowed (from {client_ip})"
        )


# =============================================================================
# API ENDPOINTS - Secured (require X-Internal-Secret)
# =============================================================================

@app.post(
    "/embed",
    response_model=EmbedResponse,
    dependencies=[Depends(verify_internal_request)],
    summary="Generate embeddings",
    description="Generate embeddings for a list of texts. INTERNAL USE ONLY."
)
async def embed(request: EmbedRequest) -> EmbedResponse:
    """
    Generate embeddings for texts.

    Args:
        request: EmbedRequest with list of texts.

    Returns:
        EmbedResponse with list of embedding vectors.
    """
    service = get_embedding_service()
    embeddings = service.embed(request.texts)
    return EmbedResponse(embeddings=embeddings.tolist())


@app.post(
    "/search",
    response_model=SearchResponse,
    dependencies=[Depends(verify_internal_request)],
    summary="Semantic search",
    description="Search FAISS index for similar vectors. INTERNAL USE ONLY."
)
async def search(request: SearchRequest) -> SearchResponse:
    """
    Search for similar vectors.

    Args:
        request: SearchRequest with query and k.

    Returns:
        SearchResponse with list of results.
    """
    service = get_embedding_service()

    # Generate query embedding
    query_embedding = service.embed([request.query])[0]

    # Search index
    results = service.search(query_embedding, request.k)

    return SearchResponse(
        results=[
            SearchResult(knowledge_id=kid, score=score)
            for kid, score in results
        ]
    )


@app.post(
    "/add",
    response_model=SuccessResponse,
    dependencies=[Depends(verify_internal_request)],
    summary="Add to index",
    description="Add vector to FAISS index. INTERNAL USE ONLY."
)
async def add(request: AddRequest) -> SuccessResponse:
    """
    Add vector to index.

    Args:
        request: AddRequest with knowledge_id and text.

    Returns:
        SuccessResponse indicating success.
    """
    service = get_embedding_service()

    # Generate embedding for text
    embedding = service.embed([request.text])[0]

    # Add to index
    success = service.add_to_index(request.knowledge_id, embedding)

    if not success:
        raise HTTPException(
            status_code=409,
            detail=f"Knowledge {request.knowledge_id} already indexed"
        )

    return SuccessResponse(success=True)


@app.post(
    "/delete",
    response_model=SuccessResponse,
    dependencies=[Depends(verify_internal_request)],
    summary="Delete from index",
    description="Mark vector as deleted (soft delete). INTERNAL USE ONLY."
)
async def delete(request: DeleteRequest) -> SuccessResponse:
    """
    Mark vector as deleted.

    Args:
        request: DeleteRequest with knowledge_id.

    Returns:
        SuccessResponse indicating success.
    """
    service = get_embedding_service()

    # Soft delete from index
    success = service.mark_deleted(request.knowledge_id)

    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Knowledge {request.knowledge_id} not found in index"
        )

    return SuccessResponse(success=True)


@app.post(
    "/reindex",
    response_model=SuccessResponse,
    dependencies=[Depends(verify_internal_request)],
    summary="Atomic re-index",
    description="Atomically delete old embedding and add new one. INTERNAL USE ONLY. ISS-014 Fix."
)
async def reindex(request: ReindexRequest) -> SuccessResponse:
    """
    Atomically re-index a knowledge entry.

    This endpoint handles the case where knowledge content has been updated
    and needs re-embedding. It performs delete and add as a single atomic
    operation to prevent the race condition where delete succeeds but add fails.

    ISS-014 Fix: Prevents orphaned knowledge entries when re-embedding fails.

    Args:
        request: ReindexRequest with knowledge_id and new text.

    Returns:
        SuccessResponse indicating success.
    """
    service = get_embedding_service()

    # Generate new embedding for text
    embedding = service.embed([request.text])[0]

    # Atomic reindex (delete old + add new in single operation)
    success = service.reindex(request.knowledge_id, embedding)

    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reindex {request.knowledge_id}"
        )

    return SuccessResponse(success=True)


@app.post(
    "/stats",
    response_model=StatsResponse,
    dependencies=[Depends(verify_internal_request)],
    summary="Get service statistics",
    description="Get embedding service statistics. INTERNAL USE ONLY."
)
async def stats() -> StatsResponse:
    """
    Get service statistics.

    Returns:
        StatsResponse with service statistics.
    """
    service = get_embedding_service()
    service_stats = service.get_stats()

    return StatsResponse(
        model=service_stats["model"],
        dimension=service_stats["dimension"],
        index_type=service_stats["index_type"],
        total_vectors=service_stats["total_vectors"],
        active_vectors=service_stats["active_vectors"],
        deleted_vectors=service_stats["deleted_vectors"]
    )


# =============================================================================
# API ENDPOINTS - Public (no auth required)
# =============================================================================

@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Health check endpoint. No authentication required."
)
async def health() -> HealthResponse:
    """
    Health check endpoint.

    Returns minimal info for security (no vector counts exposed).
    """
    return HealthResponse(
        status="ok",
        secured=True,
        binding="127.0.0.1:3101"
    )


# =============================================================================
# MAIN - Run with uvicorn
# =============================================================================

def main() -> None:
    """
    Main entry point for the embedding server.

    ISS-053 Fix: Extracted main logic to a function for module-style invocation.
    """
    import uvicorn

    # Validate environment
    if not os.getenv("DAKB_INTERNAL_SECRET"):
        logger.error("DAKB_INTERNAL_SECRET environment variable not set!")
        logger.error("Set it with: export DAKB_INTERNAL_SECRET='your-secret-here'")
        raise RuntimeError("DAKB_INTERNAL_SECRET not configured")

    # CRITICAL: Bind to loopback ONLY - not 0.0.0.0
    uvicorn.run(
        app,
        host="127.0.0.1",  # Loopback only - SECURITY
        port=3101,
        log_level="info"
    )


if __name__ == "__main__":
    main()
