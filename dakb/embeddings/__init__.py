"""
DAKB Embedding Service
======================

Sentence-transformer based embedding service with FAISS vector search.

Components:
- embedding_server.py: FastAPI server for embedding operations
- embedding_service.py: Core embedding logic
- faiss_sync_service.py: FAISS index synchronization

Usage:
    # Start the embedding service
    python -m dakb.embeddings

    # Or import for programmatic use
    from dakb.embeddings.embedding_service import EmbeddingService
"""

__all__ = ["run"]


def run() -> None:
    """Run the embedding server."""
    from dakb.embeddings.embedding_server import main
    main()
