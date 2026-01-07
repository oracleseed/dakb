"""
DAKB Embedding Service - Core EmbeddingService Class

Scalable embedding service with tiered FAISS index for semantic search.
Uses sentence-transformers with all-mpnet-base-v2 model (768 dimensions).

Version: 1.4
Created: 2025-12-07
Updated: 2025-12-07
Author: Backend Agent (Claude Opus 4.5)

Changelog v1.4:
- Added exception handling to _upgrade_to_ivf and _upgrade_to_hnsw
- Graceful failure handling: if upgrade fails, keeps current index working

Features:
- Tiered index auto-upgrade: Flat -> IVF -> HNSW
- Thread-safe operations with _index_lock
- Soft deletion with proper tracking
- Persistent storage to disk
- Null ID filtering in search results

Index Type Selection:
| Scale       | Index Type     | Search Time | Training Required |
|-------------|----------------|-------------|-------------------|
| < 10K       | IndexFlatIP    | < 5ms       | No                |
| 10K-100K    | IndexIVFFlat   | < 10ms      | Yes               |
| > 100K      | IndexHNSWFlat  | < 20ms      | No (graph-based)  |
"""

import logging
import os
import pickle
import shutil
import threading
from datetime import datetime

# Set environment variables BEFORE importing torch/transformers
# These fix semaphore leaks and multiprocessing issues on macOS
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('OMP_NUM_THREADS', '1')  # Prevents PyTorch multiprocessing issues
os.environ.setdefault('OBJC_DISABLE_INITIALIZE_FORK_SAFETY', 'YES')  # macOS fork safety

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


class EmbeddingService:
    """
    Scalable embedding service with tiered FAISS index.

    v1.2 IMPROVEMENTS:
    - Auto-upgrades index type based on vector count
    - Flat (< 10K) -> IVF (10K-100K) -> HNSW (> 100K)
    - Proper deletion tracking with reverse_map
    - Supports index rebuild from MongoDB

    v1.3 IMPROVEMENTS:
    - Added threading lock for index upgrade race condition
    - Thread-safe add/delete/upgrade operations
    - Single source of truth for deletion count via get_deletion_count()
    """

    # Thresholds for index type selection
    FLAT_THRESHOLD = 10_000      # Use Flat below this
    IVF_THRESHOLD = 100_000      # Use IVF between FLAT and this
    # Above IVF_THRESHOLD: use HNSW

    # Model configuration
    MODEL_NAME = "all-mpnet-base-v2"
    EMBEDDING_DIMENSION = 768

    def __init__(self, data_dir: str | None = None, device: str = "cpu"):
        """
        Initialize the EmbeddingService.

        Args:
            data_dir: Directory for persistent storage. Defaults to 'data/dakb_faiss'.
            device: Device to run model on ('cpu', 'cuda', 'mps'). Default 'cpu' for stability.
        """
        # v1.3: Threading lock for index operations (prevents race condition during upgrade)
        self._index_lock = threading.Lock()

        # Load model ONCE (takes ~3-5 seconds)
        # v1.5: Force CPU device by default to avoid MPS semaphore leaks
        logger.info(f"Loading embedding model ({self.MODEL_NAME}) on device: {device}...")
        self.model = SentenceTransformer(self.MODEL_NAME, device=device)

        # v1.6: Put model in eval mode and share memory to fix multiprocessing issues
        # Reference: https://github.com/fastapi/fastapi/issues/1487
        self.model.eval()
        for param in self.model.parameters():
            param.requires_grad = False
        # Note: share_memory() only works on CPU tensors, skip if on GPU
        if device == "cpu":
            try:
                self.model.share_memory()
                logger.info("Model memory shared successfully for multiprocessing safety")
            except Exception as e:
                logger.warning(f"Could not share model memory (non-critical): {e}")

        self.dimension = self.EMBEDDING_DIMENSION

        # Index and mappings
        self.index: faiss.Index | None = None
        self.id_map: dict[int, str | None] = {}           # idx -> knowledge_id (None if deleted)
        self.reverse_map: dict[str, int] = {}                 # knowledge_id -> idx (for fast lookup)
        self.vector_store: list[np.ndarray] = []              # Keep vectors for index upgrade/rebuild

        # Persistence paths
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "data", "dakb_faiss"
        )
        self.data_dir = os.path.abspath(self.data_dir)

        self.index_path = os.path.join(self.data_dir, "faiss_index.bin")
        self.id_map_path = os.path.join(self.data_dir, "faiss_id_map.pkl")
        self.vectors_path = os.path.join(self.data_dir, "faiss_vectors.npy")

        # Ensure data directory exists
        os.makedirs(self.data_dir, exist_ok=True)

        # Load existing index or create new
        self._load_or_create_index()

        logger.info(
            f"Embedding service ready. "
            f"Index: {type(self.index).__name__}, "
            f"vectors: {self.index.ntotal}"
        )

    def _create_index_for_scale(self, num_vectors: int) -> faiss.Index:
        """
        Create optimal index type based on expected scale.

        Args:
            num_vectors: Expected number of vectors in the index.

        Returns:
            FAISS index appropriate for the scale.
        """
        if num_vectors < self.FLAT_THRESHOLD:
            logger.info(f"Creating Flat index for {num_vectors} vectors")
            return faiss.IndexFlatIP(self.dimension)

        elif num_vectors < self.IVF_THRESHOLD:
            # IVF: Inverted File Index with clustering
            nlist = max(int(np.sqrt(num_vectors)), 10)  # Number of clusters
            logger.info(f"Creating IVF index with {nlist} clusters for {num_vectors} vectors")
            quantizer = faiss.IndexFlatIP(self.dimension)
            index = faiss.IndexIVFFlat(
                quantizer, self.dimension, nlist, faiss.METRIC_INNER_PRODUCT
            )
            return index

        else:
            # HNSW: Hierarchical Navigable Small World graph
            # Best for large scale, no training required
            logger.info(f"Creating HNSW index for {num_vectors} vectors")
            index = faiss.IndexHNSWFlat(self.dimension, 32)  # 32 neighbors per node
            index.metric_type = faiss.METRIC_INNER_PRODUCT
            return index

    def _maybe_upgrade_index(self) -> None:
        """
        Check if index type should be upgraded based on current size.
        Upgrades are automatic and transparent.

        v1.3: Now thread-safe with _index_lock to prevent race condition
        when multiple concurrent add_to_index calls trigger upgrade.
        """
        with self._index_lock:
            current_count = self.index.ntotal

            # Check if upgrade needed
            if current_count >= self.FLAT_THRESHOLD and isinstance(self.index, faiss.IndexFlatIP):
                self._upgrade_to_ivf()
            elif current_count >= self.IVF_THRESHOLD and isinstance(self.index, faiss.IndexIVFFlat):
                self._upgrade_to_hnsw()

    def _upgrade_to_ivf(self) -> None:
        """
        Upgrade from Flat to IVF index. Must be called with _index_lock held.

        Handles failures gracefully - if upgrade fails, keeps the current index working.
        Common failure causes: memory issues, disk I/O errors, FAISS internal errors.
        """
        if not self.vector_store:
            logger.warning("Cannot upgrade: no vectors stored")
            return

        try:
            logger.info("Upgrading index: Flat -> IVF")
            vectors = np.array(self.vector_store, dtype=np.float32)
            nlist = max(int(np.sqrt(len(vectors))), 10)

            # Create new IVF index
            quantizer = faiss.IndexFlatIP(self.dimension)
            new_index = faiss.IndexIVFFlat(
                quantizer, self.dimension, nlist, faiss.METRIC_INNER_PRODUCT
            )

            # Train on existing vectors (required for IVF)
            logger.info(f"Training IVF index on {len(vectors)} vectors...")
            new_index.train(vectors)
            new_index.add(vectors)

            self.index = new_index
            self._save_index()
            logger.info(f"Upgraded to IVF with {nlist} clusters successfully")

        except MemoryError as e:
            logger.error(f"Failed to upgrade to IVF: insufficient memory - {e}. Keeping current Flat index.")
            # Don't re-raise - keep the current index working

        except Exception as e:
            logger.error(f"Failed to upgrade to IVF: {e}. Keeping current Flat index.")
            # Don't re-raise - keep the current index working

    def _upgrade_to_hnsw(self) -> None:
        """
        Upgrade from IVF to HNSW index. Must be called with _index_lock held.

        Handles failures gracefully - if upgrade fails, keeps the current index working.
        Common failure causes: memory issues, disk I/O errors, FAISS internal errors.
        """
        if not self.vector_store:
            logger.warning("Cannot upgrade: no vectors stored")
            return

        try:
            logger.info("Upgrading index: IVF -> HNSW")
            vectors = np.array(self.vector_store, dtype=np.float32)

            # Create HNSW index
            new_index = faiss.IndexHNSWFlat(self.dimension, 32)
            new_index.metric_type = faiss.METRIC_INNER_PRODUCT

            # Add vectors (no training needed for HNSW)
            new_index.add(vectors)

            self.index = new_index
            self._save_index()
            logger.info(f"Upgraded to HNSW with {new_index.ntotal} vectors successfully")

        except MemoryError as e:
            logger.error(f"Failed to upgrade to HNSW: insufficient memory - {e}. Keeping current IVF index.")
            # Don't re-raise - keep the current index working

        except Exception as e:
            logger.error(f"Failed to upgrade to HNSW: {e}. Keeping current IVF index.")
            # Don't re-raise - keep the current index working

    def embed(self, texts: list[str]) -> np.ndarray:
        """
        Generate embeddings for texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            numpy array of shape (len(texts), 768) with normalized embeddings.
        """
        embeddings = self.model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False  # Disable progress bar to prevent MPS semaphore leaks
        )
        return embeddings.astype(np.float32)

    def add_to_index(self, knowledge_id: str, embedding: np.ndarray) -> bool:
        """
        Add vector to FAISS index with proper tracking.

        Args:
            knowledge_id: Unique identifier for the knowledge entry.
            embedding: Vector embedding (768 dimensions).

        Returns:
            True if added successfully, False if already exists.
        """
        # Check for duplicate
        if knowledge_id in self.reverse_map:
            logger.warning(f"Knowledge {knowledge_id} already indexed, skipping")
            return False

        with self._index_lock:
            idx = len(self.vector_store)

            # Ensure embedding is the right shape and type
            embedding = embedding.astype(np.float32).flatten()
            if len(embedding) != self.dimension:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self.dimension}, "
                    f"got {len(embedding)}"
                )

            # Store vector for potential rebuild/upgrade
            self.vector_store.append(embedding.copy())

            # Handle IVF special case (needs training)
            if isinstance(self.index, faiss.IndexIVFFlat) and not self.index.is_trained:
                # Batch training will happen on upgrade
                pass
            else:
                self.index.add(embedding.reshape(1, -1))

            # Update mappings
            self.id_map[idx] = knowledge_id
            self.reverse_map[knowledge_id] = idx

        # Check for index upgrade every 1000 additions
        if idx > 0 and idx % 1000 == 0:
            self._maybe_upgrade_index()

        self._save_index()
        return True

    def search(
        self,
        query_embedding: np.ndarray,
        k: int = 5
    ) -> list[tuple[str, float]]:
        """
        Search for similar vectors.
        Automatically filters deleted entries and fetches extras to compensate.

        Args:
            query_embedding: Query vector (768 dimensions).
            k: Number of results to return.

        Returns:
            List of (knowledge_id, similarity_score) tuples.
        """
        if self.index.ntotal == 0:
            return []

        # Calculate how many extra results to fetch to account for deletions
        deletion_count = self.get_deletion_count()
        fetch_k = min(self.index.ntotal, max(k * 2, k + deletion_count + 5))

        # Ensure query is the right shape
        query_embedding = query_embedding.astype(np.float32).reshape(1, -1)

        # Set search parameters for IVF
        if isinstance(self.index, faiss.IndexIVFFlat):
            self.index.nprobe = min(10, self.index.nlist)  # Search 10 clusters

        distances, indices = self.index.search(query_embedding, fetch_k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:  # FAISS returns -1 for invalid
                continue

            kid = self.id_map.get(idx)
            if kid is None:  # Marked as deleted - SKIP
                continue

            results.append((kid, float(dist)))

            if len(results) >= k:
                break

        return results

    def mark_deleted(self, knowledge_id: str) -> bool:
        """
        Soft delete: mark vector as deleted without removing from index.
        Actual removal happens during periodic rebuild.

        Args:
            knowledge_id: ID of the knowledge entry to delete.

        Returns:
            True if deleted, False if not found.
        """
        if knowledge_id not in self.reverse_map:
            return False

        with self._index_lock:
            idx = self.reverse_map[knowledge_id]
            self.id_map[idx] = None  # Mark as deleted
            del self.reverse_map[knowledge_id]

        self._save_index()
        return True

    def delete_from_index(self, knowledge_id: str) -> bool:
        """
        Alias for mark_deleted for API consistency.

        Args:
            knowledge_id: ID of the knowledge entry to delete.

        Returns:
            True if deleted, False if not found.
        """
        return self.mark_deleted(knowledge_id)

    def reindex(self, knowledge_id: str, embedding: np.ndarray) -> bool:
        """
        Atomically re-index a knowledge entry (delete old + add new).

        This method handles the case where knowledge content has been updated
        and needs re-embedding. It performs delete and add as a single atomic
        operation to prevent the race condition where delete succeeds but add fails.

        ISS-014 Fix: Atomic delete+add to prevent orphaned knowledge entries.

        Args:
            knowledge_id: Unique identifier for the knowledge entry.
            embedding: New vector embedding (768 dimensions).

        Returns:
            True if reindexed successfully, False if not found in index.
        """
        with self._index_lock:
            # Check if exists
            if knowledge_id not in self.reverse_map:
                logger.warning(f"Knowledge {knowledge_id} not in index, adding as new")
                # Fall through to add it as new
            else:
                # Delete old entry (mark as deleted)
                idx = self.reverse_map[knowledge_id]
                self.id_map[idx] = None  # Mark old slot as deleted
                del self.reverse_map[knowledge_id]
                logger.debug(f"Marked old embedding for {knowledge_id} as deleted")

            # Add new entry
            idx = len(self.vector_store)

            # Ensure embedding is the right shape and type
            embedding = embedding.astype(np.float32).flatten()
            if len(embedding) != self.dimension:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self.dimension}, "
                    f"got {len(embedding)}"
                )

            # Store vector for potential rebuild/upgrade
            self.vector_store.append(embedding.copy())

            # Handle IVF special case (needs training)
            if isinstance(self.index, faiss.IndexIVFFlat) and not self.index.is_trained:
                pass
            else:
                self.index.add(embedding.reshape(1, -1))

            # Update mappings
            self.id_map[idx] = knowledge_id
            self.reverse_map[knowledge_id] = idx

        # Check for index upgrade every 1000 additions
        if idx > 0 and idx % 1000 == 0:
            self._maybe_upgrade_index()

        self._save_index()
        logger.info(f"Reindexed knowledge {knowledge_id}")
        return True

    def get_active_ids(self) -> set[str]:
        """
        Get set of all non-deleted knowledge IDs.

        Returns:
            Set of active knowledge IDs.
        """
        return set(kid for kid in self.id_map.values() if kid is not None)

    def get_deletion_count(self) -> int:
        """
        Count vectors marked as deleted.
        This is the SINGLE SOURCE OF TRUTH for deletion count (v1.3 fix).

        Returns:
            Number of deleted entries in the index.
        """
        return sum(1 for kid in self.id_map.values() if kid is None)

    def rebuild_from_documents(self, documents: list[dict]) -> None:
        """
        Rebuild entire index from document list.
        Used for sync after high deletion ratio.

        Args:
            documents: List of dicts with 'knowledge_id', 'title', and 'content' keys.
        """
        logger.info(f"Rebuilding index from {len(documents)} documents...")

        with self._index_lock:
            # Clear existing
            self.id_map = {}
            self.reverse_map = {}
            self.vector_store = []

            if not documents:
                self.index = self._create_index_for_scale(0)
                self._save_index()
                return

            # Create appropriate index for scale
            self.index = self._create_index_for_scale(len(documents))

            # Generate embeddings
            texts = [f"{doc['title']} {doc['content']}" for doc in documents]
            embeddings = self.model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False  # Disable progress bar to prevent MPS semaphore leaks
            ).astype(np.float32)

            # Train if IVF
            if isinstance(self.index, faiss.IndexIVFFlat):
                logger.info("Training IVF index...")
                self.index.train(embeddings)

            # Add to index
            self.index.add(embeddings)

            # Update mappings
            for i, doc in enumerate(documents):
                self.id_map[i] = doc["knowledge_id"]
                self.reverse_map[doc["knowledge_id"]] = i
                self.vector_store.append(embeddings[i])

            self._save_index()

        logger.info(f"Rebuild complete: {self.index.ntotal} vectors indexed")

    def backup_index(self) -> str:
        """
        Create backup before rebuild.

        Returns:
            Path to the backup directory.
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(self.data_dir, "backups", f"faiss_{timestamp}")
        os.makedirs(backup_dir, exist_ok=True)

        for path in [self.index_path, self.id_map_path, self.vectors_path]:
            if os.path.exists(path):
                shutil.copy(path, os.path.join(backup_dir, os.path.basename(path)))

        logger.info(f"Index backed up to {backup_dir}")
        return backup_dir

    def _save_index(self) -> None:
        """Persist index and mappings to disk."""
        os.makedirs(self.data_dir, exist_ok=True)

        faiss.write_index(self.index, self.index_path)

        with open(self.id_map_path, 'wb') as f:
            pickle.dump({
                'id_map': self.id_map,
                'reverse_map': self.reverse_map
            }, f)

        if self.vector_store:
            np.save(self.vectors_path, np.array(self.vector_store, dtype=np.float32))

    def _load_or_create_index(self) -> None:
        """Load existing index or create new one."""
        if os.path.exists(self.index_path) and os.path.exists(self.id_map_path):
            logger.info("Loading existing FAISS index...")
            self.index = faiss.read_index(self.index_path)

            with open(self.id_map_path, 'rb') as f:
                data = pickle.load(f)
                # Backward compat: older versions stored id_map directly
                if isinstance(data, dict) and 'id_map' in data:
                    self.id_map = data.get('id_map', {})
                    self.reverse_map = data.get('reverse_map', {})
                else:
                    self.id_map = data
                    self.reverse_map = {v: k for k, v in self.id_map.items() if v is not None}

            if os.path.exists(self.vectors_path):
                self.vector_store = list(np.load(self.vectors_path))
        else:
            logger.info("Creating new FAISS index...")
            self.index = self._create_index_for_scale(0)

    def get_stats(self) -> dict:
        """
        Get statistics about the embedding service.

        Returns:
            Dictionary with service statistics.
        """
        return {
            "model": self.MODEL_NAME,
            "dimension": self.dimension,
            "index_type": type(self.index).__name__,
            "total_vectors": self.index.ntotal,
            "active_vectors": len(self.reverse_map),
            "deleted_vectors": self.get_deletion_count(),
            "data_dir": self.data_dir
        }
