"""
DAKB Local Cache

In-memory caching layer for the local proxy.
Provides LRU eviction and TTL-based expiration.

Version: 1.0.1
Created: 2025-12-17
Updated: 2025-12-17 - Added thread-safety and automatic cleanup trigger
"""
from __future__ import annotations

import json
import logging
import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional, TypeVar, Generic

logger = logging.getLogger(__name__)

# Cleanup configuration
CLEANUP_INTERVAL_OPERATIONS = 100  # Run cleanup every N operations

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """Cache entry with value and expiration."""
    value: T
    expires_at: datetime
    hit_count: int = 0

    def is_expired(self) -> bool:
        """Check if entry is expired."""
        return datetime.now(timezone.utc) >= self.expires_at


class LocalCache:
    """
    Thread-safe in-memory LRU cache with TTL support.

    Features:
    - LRU eviction when max entries exceeded
    - TTL-based expiration per entry
    - Hit count tracking
    - Optional disk persistence
    - Thread-safe operations

    Usage:
        cache = LocalCache(max_entries=1000, default_ttl=300)

        # Set with default TTL
        cache.set("key1", {"data": "value"})

        # Set with custom TTL
        cache.set("key2", {"data": "value"}, ttl=60)

        # Get with optional default
        value = cache.get("key1")  # Returns None if not found/expired
        value = cache.get("key3", default={})  # Returns {} if not found

        # Delete
        cache.delete("key1")

        # Clear all
        cache.clear()
    """

    def __init__(
        self,
        max_entries: int = 1000,
        default_ttl: int = 300,
        persist_path: Optional[Path] = None,
    ):
        """
        Initialize cache.

        Args:
            max_entries: Maximum number of entries (LRU eviction)
            default_ttl: Default TTL in seconds
            persist_path: Optional path for disk persistence
        """
        self.max_entries = max_entries
        self.default_ttl = default_ttl
        self.persist_path = persist_path

        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "expirations": 0,
        }
        self._operation_count = 0  # Counter for automatic cleanup trigger

        # Load from disk if persistence enabled
        if persist_path and persist_path.exists():
            self._load_from_disk()

    def get(self, key: str, default: Optional[T] = None) -> Optional[T]:
        """
        Get value from cache.

        Args:
            key: Cache key
            default: Default value if not found

        Returns:
            Cached value or default
        """
        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._stats["misses"] += 1
                return default

            if entry.is_expired():
                del self._cache[key]
                self._stats["expirations"] += 1
                self._stats["misses"] += 1
                return default

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            entry.hit_count += 1
            self._stats["hits"] += 1

            return entry.value

    def set(
        self,
        key: str,
        value: T,
        ttl: Optional[int] = None,
    ) -> None:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Optional TTL override (seconds)
        """
        with self._lock:
            # Calculate expiration
            ttl_seconds = ttl if ttl is not None else self.default_ttl
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

            # Create entry
            entry = CacheEntry(value=value, expires_at=expires_at)

            # Remove if exists (to update position)
            if key in self._cache:
                del self._cache[key]

            # Add to end
            self._cache[key] = entry

            # Evict if over limit
            while len(self._cache) > self.max_entries:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                self._stats["evictions"] += 1

            # Automatic cleanup trigger
            self._operation_count += 1
            if self._operation_count >= CLEANUP_INTERVAL_OPERATIONS:
                self._operation_count = 0
                # Release lock during cleanup to avoid blocking
                self._lock.release()
                try:
                    self.cleanup_expired()
                finally:
                    self._lock.acquire()

    def delete(self, key: str) -> bool:
        """
        Delete key from cache.

        Args:
            key: Cache key

        Returns:
            True if key existed
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> int:
        """
        Clear all entries.

        Returns:
            Number of entries cleared
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def cleanup_expired(self) -> int:
        """
        Remove all expired entries.

        Returns:
            Number of entries removed
        """
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired()
            ]
            for key in expired_keys:
                del self._cache[key]
                self._stats["expirations"] += 1
            return len(expired_keys)

    def stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Statistics dictionary
        """
        with self._lock:
            total_requests = self._stats["hits"] + self._stats["misses"]
            hit_rate = (
                self._stats["hits"] / total_requests
                if total_requests > 0
                else 0.0
            )

            return {
                "entries": len(self._cache),
                "max_entries": self.max_entries,
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "hit_rate": round(hit_rate, 4),
                "evictions": self._stats["evictions"],
                "expirations": self._stats["expirations"],
            }

    def _load_from_disk(self) -> None:
        """Load cache from disk. Thread-safe."""
        if not self.persist_path:
            return

        try:
            with open(self.persist_path) as f:
                data = json.load(f)

            now = datetime.now(timezone.utc)
            loaded_count = 0

            # Acquire lock when modifying self._cache
            with self._lock:
                for key, entry_data in data.items():
                    expires_at = datetime.fromisoformat(entry_data["expires_at"])
                    if expires_at > now:
                        self._cache[key] = CacheEntry(
                            value=entry_data["value"],
                            expires_at=expires_at,
                            hit_count=entry_data.get("hit_count", 0),
                        )
                        loaded_count += 1

            logger.info(f"Loaded {loaded_count} entries from disk cache")
        except Exception as e:
            logger.warning(f"Failed to load disk cache: {e}")

    def save_to_disk(self) -> None:
        """Save cache to disk."""
        if not self.persist_path:
            return

        try:
            with self._lock:
                data = {}
                for key, entry in self._cache.items():
                    if not entry.is_expired():
                        data[key] = {
                            "value": entry.value,
                            "expires_at": entry.expires_at.isoformat(),
                            "hit_count": entry.hit_count,
                        }

            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.persist_path, "w") as f:
                json.dump(data, f)

            logger.info(f"Saved {len(data)} entries to disk cache")
        except Exception as e:
            logger.warning(f"Failed to save disk cache: {e}")

    def __len__(self) -> int:
        """Get number of entries."""
        with self._lock:
            return len(self._cache)

    def __contains__(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            if entry.is_expired():
                del self._cache[key]
                return False
            return True


class SearchCache(LocalCache):
    """
    Specialized cache for search results.

    Generates cache keys from search parameters.
    """

    def __init__(
        self,
        max_entries: int = 500,
        default_ttl: int = 60,  # 1 minute for search results
    ):
        super().__init__(max_entries=max_entries, default_ttl=default_ttl)

    def make_key(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.3,
        category: Optional[str] = None,
    ) -> str:
        """
        Generate cache key from search parameters.

        Args:
            query: Search query
            limit: Result limit
            min_score: Minimum similarity score
            category: Optional category filter

        Returns:
            Cache key
        """
        params = f"{query}:{limit}:{min_score}:{category}"
        return hashlib.sha256(params.encode()).hexdigest()[:16]

    def get_search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.3,
        category: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Get cached search results."""
        key = self.make_key(query, limit, min_score, category)
        return self.get(key)

    def set_search(
        self,
        query: str,
        results: dict[str, Any],
        limit: int = 5,
        min_score: float = 0.3,
        category: Optional[str] = None,
    ) -> None:
        """Cache search results."""
        key = self.make_key(query, limit, min_score, category)
        self.set(key, results)


class KnowledgeCache(LocalCache):
    """
    Specialized cache for knowledge entries.

    Caches individual knowledge entries by ID.
    """

    def __init__(
        self,
        max_entries: int = 500,
        default_ttl: int = 300,  # 5 minutes
    ):
        super().__init__(max_entries=max_entries, default_ttl=default_ttl)

    def get_knowledge(self, knowledge_id: str) -> Optional[dict[str, Any]]:
        """Get cached knowledge entry."""
        return self.get(f"kn:{knowledge_id}")

    def set_knowledge(
        self,
        knowledge_id: str,
        entry: dict[str, Any],
        ttl: Optional[int] = None,
    ) -> None:
        """Cache knowledge entry."""
        self.set(f"kn:{knowledge_id}", entry, ttl)

    def invalidate_knowledge(self, knowledge_id: str) -> bool:
        """Invalidate cached knowledge entry."""
        return self.delete(f"kn:{knowledge_id}")
