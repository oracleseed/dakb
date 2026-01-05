"""
DAKB Prometheus Metrics - Phase 6.3 Production Hardening

Metrics collection and export for monitoring the DAKB system.

Features:
- Prometheus-compatible metrics export
- Request latency histograms
- Error rate tracking
- FAISS index size monitoring
- MongoDB connection pool metrics
- Custom DAKB-specific metrics

Version: 1.0.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Usage:
    # Initialize metrics
    from backend.dakb_service.monitoring import get_metrics

    metrics = get_metrics()
    metrics.record_request("/api/v1/knowledge", "GET", 200, 45.2)

    # Get Prometheus format
    output = metrics.export_prometheus()
"""

import logging
import os
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# Add project root to path
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
sys.path.insert(0, PROJECT_ROOT)

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# METRIC TYPES
# =============================================================================


class MetricType(str, Enum):
    """Types of metrics."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class MetricValue:
    """Single metric value with labels."""

    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class Counter:
    """
    Counter metric - monotonically increasing value.

    Used for counting events like requests, errors, etc.
    """

    def __init__(self, name: str, description: str, labels: Optional[List[str]] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, **labels) -> None:
        """Increment counter by value."""
        if value < 0:
            raise ValueError("Counter can only be incremented")

        label_key = self._make_label_key(labels)
        with self._lock:
            self._values[label_key] += value

    def get(self, **labels) -> float:
        """Get current counter value."""
        label_key = self._make_label_key(labels)
        with self._lock:
            return self._values.get(label_key, 0.0)

    def _make_label_key(self, labels: Dict[str, str]) -> tuple:
        """Create hashable key from labels."""
        return tuple(sorted((k, str(v)) for k, v in labels.items()))

    def get_all(self) -> List[MetricValue]:
        """Get all values with labels."""
        with self._lock:
            return [
                MetricValue(
                    value=value,
                    labels=dict(label_key),
                )
                for label_key, value in self._values.items()
            ]


class Gauge:
    """
    Gauge metric - value that can go up and down.

    Used for current values like queue size, active connections, etc.
    """

    def __init__(self, name: str, description: str, labels: Optional[List[str]] = None):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self._values: Dict[tuple, float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, **labels) -> None:
        """Set gauge value."""
        label_key = self._make_label_key(labels)
        with self._lock:
            self._values[label_key] = value

    def inc(self, value: float = 1.0, **labels) -> None:
        """Increment gauge value."""
        label_key = self._make_label_key(labels)
        with self._lock:
            self._values[label_key] = self._values.get(label_key, 0.0) + value

    def dec(self, value: float = 1.0, **labels) -> None:
        """Decrement gauge value."""
        label_key = self._make_label_key(labels)
        with self._lock:
            self._values[label_key] = self._values.get(label_key, 0.0) - value

    def get(self, **labels) -> float:
        """Get current gauge value."""
        label_key = self._make_label_key(labels)
        with self._lock:
            return self._values.get(label_key, 0.0)

    def _make_label_key(self, labels: Dict[str, str]) -> tuple:
        """Create hashable key from labels."""
        return tuple(sorted((k, str(v)) for k, v in labels.items()))

    def get_all(self) -> List[MetricValue]:
        """Get all values with labels."""
        with self._lock:
            return [
                MetricValue(
                    value=value,
                    labels=dict(label_key),
                )
                for label_key, value in self._values.items()
            ]


class Histogram:
    """
    Histogram metric - distribution of values.

    Used for latency measurements, request sizes, etc.
    """

    DEFAULT_BUCKETS = (
        0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0,
        2.5, 5.0, 7.5, 10.0, float("inf")
    )

    def __init__(
        self,
        name: str,
        description: str,
        labels: Optional[List[str]] = None,
        buckets: Optional[Tuple[float, ...]] = None,
    ):
        self.name = name
        self.description = description
        self.label_names = labels or []
        self.buckets = buckets or self.DEFAULT_BUCKETS

        self._bucket_counts: Dict[tuple, Dict[float, int]] = defaultdict(
            lambda: {b: 0 for b in self.buckets}
        )
        self._sums: Dict[tuple, float] = defaultdict(float)
        self._counts: Dict[tuple, int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels) -> None:
        """Record an observation."""
        label_key = self._make_label_key(labels)
        with self._lock:
            self._sums[label_key] += value
            self._counts[label_key] += 1

            for bucket in self.buckets:
                if value <= bucket:
                    self._bucket_counts[label_key][bucket] += 1

    def get_count(self, **labels) -> int:
        """Get total observation count."""
        label_key = self._make_label_key(labels)
        with self._lock:
            return self._counts.get(label_key, 0)

    def get_sum(self, **labels) -> float:
        """Get sum of all observations."""
        label_key = self._make_label_key(labels)
        with self._lock:
            return self._sums.get(label_key, 0.0)

    def _make_label_key(self, labels: Dict[str, str]) -> tuple:
        """Create hashable key from labels."""
        return tuple(sorted((k, str(v)) for k, v in labels.items()))

    def get_all(self) -> Dict[tuple, Dict[str, Any]]:
        """Get all histogram data."""
        with self._lock:
            result = {}
            for label_key in set(self._counts.keys()):
                result[label_key] = {
                    "buckets": dict(self._bucket_counts[label_key]),
                    "sum": self._sums[label_key],
                    "count": self._counts[label_key],
                }
            return result


# =============================================================================
# METRICS REGISTRY
# =============================================================================


class MetricsRegistry:
    """
    Registry for all metrics.

    Maintains a collection of all metrics and provides methods for
    accessing and exporting them.
    """

    def __init__(self, namespace: str = "dakb"):
        self.namespace = namespace
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(
        self,
        name: str,
        description: str,
        labels: Optional[List[str]] = None,
    ) -> Counter:
        """Get or create a counter."""
        full_name = f"{self.namespace}_{name}"
        with self._lock:
            if full_name not in self._counters:
                self._counters[full_name] = Counter(full_name, description, labels)
            return self._counters[full_name]

    def gauge(
        self,
        name: str,
        description: str,
        labels: Optional[List[str]] = None,
    ) -> Gauge:
        """Get or create a gauge."""
        full_name = f"{self.namespace}_{name}"
        with self._lock:
            if full_name not in self._gauges:
                self._gauges[full_name] = Gauge(full_name, description, labels)
            return self._gauges[full_name]

    def histogram(
        self,
        name: str,
        description: str,
        labels: Optional[List[str]] = None,
        buckets: Optional[Tuple[float, ...]] = None,
    ) -> Histogram:
        """Get or create a histogram."""
        full_name = f"{self.namespace}_{name}"
        with self._lock:
            if full_name not in self._histograms:
                self._histograms[full_name] = Histogram(
                    full_name, description, labels, buckets
                )
            return self._histograms[full_name]

    def export_prometheus(self) -> str:
        """
        Export all metrics in Prometheus text format.

        Returns:
            Prometheus-compatible metrics string
        """
        lines = []

        # Export counters
        for name, counter in self._counters.items():
            lines.append(f"# HELP {name} {counter.description}")
            lines.append(f"# TYPE {name} counter")
            for mv in counter.get_all():
                label_str = self._format_labels(mv.labels)
                lines.append(f"{name}{label_str} {mv.value}")

        # Export gauges
        for name, gauge in self._gauges.items():
            lines.append(f"# HELP {name} {gauge.description}")
            lines.append(f"# TYPE {name} gauge")
            for mv in gauge.get_all():
                label_str = self._format_labels(mv.labels)
                lines.append(f"{name}{label_str} {mv.value}")

        # Export histograms
        for name, histogram in self._histograms.items():
            lines.append(f"# HELP {name} {histogram.description}")
            lines.append(f"# TYPE {name} histogram")
            for label_key, data in histogram.get_all().items():
                labels = dict(label_key)
                label_str = self._format_labels(labels)

                # Bucket values (cumulative)
                cumulative = 0
                for bucket, count in sorted(data["buckets"].items()):
                    cumulative += count
                    if bucket == float("inf"):
                        bucket_label = "+Inf"
                    else:
                        bucket_label = str(bucket)
                    bucket_labels = {**labels, "le": bucket_label}
                    bucket_label_str = self._format_labels(bucket_labels)
                    lines.append(f"{name}_bucket{bucket_label_str} {cumulative}")

                # Sum and count
                lines.append(f"{name}_sum{label_str} {data['sum']}")
                lines.append(f"{name}_count{label_str} {data['count']}")

        return "\n".join(lines) + "\n"

    def _format_labels(self, labels: Dict[str, str]) -> str:
        """Format labels for Prometheus output."""
        if not labels:
            return ""
        label_parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(label_parts) + "}"

    def get_all_metrics(self) -> Dict[str, Any]:
        """Get all metrics as dictionary."""
        return {
            "counters": {
                name: {
                    "description": c.description,
                    "values": [
                        {"labels": mv.labels, "value": mv.value}
                        for mv in c.get_all()
                    ],
                }
                for name, c in self._counters.items()
            },
            "gauges": {
                name: {
                    "description": g.description,
                    "values": [
                        {"labels": mv.labels, "value": mv.value}
                        for mv in g.get_all()
                    ],
                }
                for name, g in self._gauges.items()
            },
            "histograms": {
                name: {
                    "description": h.description,
                    "values": h.get_all(),
                }
                for name, h in self._histograms.items()
            },
        }


# =============================================================================
# DAKB-SPECIFIC METRICS COLLECTOR
# =============================================================================


class MetricsCollector:
    """
    DAKB-specific metrics collector.

    Provides high-level methods for recording DAKB operations.
    """

    def __init__(self, registry: Optional[MetricsRegistry] = None):
        self.registry = registry or MetricsRegistry()
        self._initialize_metrics()

    def _initialize_metrics(self) -> None:
        """Initialize all DAKB metrics."""
        # Request metrics
        self.requests_total = self.registry.counter(
            "http_requests_total",
            "Total HTTP requests",
            labels=["method", "endpoint", "status"],
        )

        self.request_latency = self.registry.histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds",
            labels=["method", "endpoint"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf")),
        )

        # Knowledge metrics
        self.knowledge_total = self.registry.gauge(
            "knowledge_entries_total",
            "Total knowledge entries",
            labels=["status", "category"],
        )

        self.knowledge_created = self.registry.counter(
            "knowledge_created_total",
            "Total knowledge entries created",
            labels=["category", "content_type"],
        )

        self.knowledge_searches = self.registry.counter(
            "knowledge_searches_total",
            "Total knowledge searches",
            labels=["has_results"],
        )

        # Search metrics
        self.search_latency = self.registry.histogram(
            "search_duration_seconds",
            "Search latency in seconds",
            labels=["search_type"],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, float("inf")),
        )

        self.faiss_search_latency = self.registry.histogram(
            "faiss_search_duration_seconds",
            "FAISS search latency in seconds",
            buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, float("inf")),
        )

        # FAISS index metrics
        self.faiss_index_size = self.registry.gauge(
            "faiss_index_vectors_total",
            "Total vectors in FAISS index",
        )

        self.faiss_index_memory = self.registry.gauge(
            "faiss_index_memory_bytes",
            "Memory used by FAISS index",
        )

        # MongoDB metrics
        self.mongodb_operations = self.registry.counter(
            "mongodb_operations_total",
            "Total MongoDB operations",
            labels=["operation", "collection"],
        )

        self.mongodb_latency = self.registry.histogram(
            "mongodb_operation_duration_seconds",
            "MongoDB operation latency in seconds",
            labels=["operation", "collection"],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, float("inf")),
        )

        self.mongodb_connections_active = self.registry.gauge(
            "mongodb_connections_active",
            "Active MongoDB connections",
        )

        self.mongodb_connections_available = self.registry.gauge(
            "mongodb_connections_available",
            "Available MongoDB connections in pool",
        )

        # Message metrics
        self.messages_sent = self.registry.counter(
            "messages_sent_total",
            "Total messages sent",
            labels=["type", "priority"],
        )

        self.messages_received = self.registry.counter(
            "messages_received_total",
            "Total messages received",
            labels=["type", "priority"],
        )

        self.messages_pending = self.registry.gauge(
            "messages_pending_total",
            "Pending messages waiting for delivery",
        )

        # Agent metrics
        self.agents_active = self.registry.gauge(
            "agents_active_total",
            "Total active agents",
            labels=["type"],
        )

        self.agent_heartbeats = self.registry.counter(
            "agent_heartbeats_total",
            "Total agent heartbeats",
            labels=["agent_id"],
        )

        # Session metrics
        self.sessions_active = self.registry.gauge(
            "sessions_active_total",
            "Total active sessions",
        )

        self.session_duration = self.registry.histogram(
            "session_duration_seconds",
            "Session duration in seconds",
            buckets=(60, 300, 600, 1800, 3600, 7200, 14400, 28800, float("inf")),
        )

        # Error metrics
        self.errors_total = self.registry.counter(
            "errors_total",
            "Total errors",
            labels=["type", "endpoint"],
        )

        # Rate limiting metrics
        self.rate_limit_hits = self.registry.counter(
            "rate_limit_hits_total",
            "Total rate limit hits",
            labels=["endpoint"],
        )

        # Authentication metrics
        self.auth_attempts = self.registry.counter(
            "auth_attempts_total",
            "Total authentication attempts",
            labels=["success", "method"],
        )

        # Voting metrics
        self.votes_cast = self.registry.counter(
            "votes_cast_total",
            "Total votes cast",
            labels=["vote_type"],
        )

        # System metrics
        self.uptime_seconds = self.registry.gauge(
            "uptime_seconds",
            "Service uptime in seconds",
        )

        self._start_time = time.time()

    def record_request(
        self,
        endpoint: str,
        method: str,
        status: int,
        latency_seconds: float,
    ) -> None:
        """
        Record HTTP request metrics.

        Args:
            endpoint: Request endpoint path
            method: HTTP method
            status: Response status code
            latency_seconds: Request latency in seconds
        """
        self.requests_total.inc(method=method, endpoint=endpoint, status=str(status))
        self.request_latency.observe(latency_seconds, method=method, endpoint=endpoint)

        if status >= 400:
            error_type = "client_error" if status < 500 else "server_error"
            self.errors_total.inc(type=error_type, endpoint=endpoint)

    def record_search(
        self,
        latency_seconds: float,
        result_count: int,
        search_type: str = "semantic",
        faiss_latency_seconds: Optional[float] = None,
    ) -> None:
        """
        Record search metrics.

        Args:
            latency_seconds: Total search latency
            result_count: Number of results returned
            search_type: Type of search (semantic, keyword, etc.)
            faiss_latency_seconds: FAISS-specific latency if available
        """
        self.knowledge_searches.inc(has_results=str(result_count > 0))
        self.search_latency.observe(latency_seconds, search_type=search_type)

        if faiss_latency_seconds is not None:
            self.faiss_search_latency.observe(faiss_latency_seconds)

    def record_knowledge_created(
        self,
        category: str,
        content_type: str,
    ) -> None:
        """Record knowledge creation."""
        self.knowledge_created.inc(category=category, content_type=content_type)

    def record_mongodb_operation(
        self,
        operation: str,
        collection: str,
        latency_seconds: float,
    ) -> None:
        """Record MongoDB operation."""
        self.mongodb_operations.inc(operation=operation, collection=collection)
        self.mongodb_latency.observe(
            latency_seconds, operation=operation, collection=collection
        )

    def record_message_sent(
        self,
        message_type: str,
        priority: str,
    ) -> None:
        """Record message sent."""
        self.messages_sent.inc(type=message_type, priority=priority)

    def record_auth_attempt(
        self,
        success: bool,
        method: str = "jwt",
    ) -> None:
        """Record authentication attempt."""
        self.auth_attempts.inc(success=str(success).lower(), method=method)

    def record_vote(self, vote_type: str) -> None:
        """Record vote cast."""
        self.votes_cast.inc(vote_type=vote_type)

    def record_rate_limit_hit(self, endpoint: str) -> None:
        """Record rate limit hit."""
        self.rate_limit_hits.inc(endpoint=endpoint)

    def record_error(self, error_type: str, endpoint: str = "") -> None:
        """Record error occurrence."""
        self.errors_total.inc(type=error_type, endpoint=endpoint)

    def update_faiss_index_metrics(
        self,
        vector_count: int,
        memory_bytes: Optional[int] = None,
    ) -> None:
        """Update FAISS index metrics."""
        self.faiss_index_size.set(vector_count)
        if memory_bytes is not None:
            self.faiss_index_memory.set(memory_bytes)

    def update_mongodb_pool_metrics(
        self,
        active: int,
        available: int,
    ) -> None:
        """Update MongoDB connection pool metrics."""
        self.mongodb_connections_active.set(active)
        self.mongodb_connections_available.set(available)

    def update_agent_count(self, agent_type: str, count: int) -> None:
        """Update active agent count."""
        self.agents_active.set(count, type=agent_type)

    def update_session_count(self, count: int) -> None:
        """Update active session count."""
        self.sessions_active.set(count)

    def update_pending_messages(self, count: int) -> None:
        """Update pending message count."""
        self.messages_pending.set(count)

    def update_knowledge_count(
        self,
        count: int,
        status: str = "active",
        category: str = "all",
    ) -> None:
        """Update knowledge entry count."""
        self.knowledge_total.set(count, status=status, category=category)

    def update_uptime(self) -> None:
        """Update uptime metric."""
        uptime = time.time() - self._start_time
        self.uptime_seconds.set(uptime)

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus format."""
        self.update_uptime()
        return self.registry.export_prometheus()

    def get_metrics(self) -> Dict[str, Any]:
        """Get all metrics as dictionary."""
        self.update_uptime()
        return self.registry.get_all_metrics()


# =============================================================================
# GLOBAL METRICS INSTANCE
# =============================================================================

_metrics_instance: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """
    Get global metrics collector instance.

    Returns:
        MetricsCollector singleton instance
    """
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = MetricsCollector()
    return _metrics_instance


def record_request(
    endpoint: str,
    method: str,
    status: int,
    latency_ms: float,
) -> None:
    """
    Convenience function to record request.

    Args:
        endpoint: Request endpoint
        method: HTTP method
        status: Response status
        latency_ms: Latency in milliseconds
    """
    get_metrics().record_request(endpoint, method, status, latency_ms / 1000.0)


def record_search_latency(
    latency_ms: float,
    result_count: int,
    faiss_latency_ms: Optional[float] = None,
) -> None:
    """
    Convenience function to record search latency.

    Args:
        latency_ms: Total latency in milliseconds
        result_count: Number of results
        faiss_latency_ms: FAISS latency in milliseconds
    """
    faiss_seconds = faiss_latency_ms / 1000.0 if faiss_latency_ms else None
    get_metrics().record_search(latency_ms / 1000.0, result_count, "semantic", faiss_seconds)


def record_error(error_type: str, endpoint: str = "") -> None:
    """
    Convenience function to record error.

    Args:
        error_type: Type of error
        endpoint: Affected endpoint
    """
    get_metrics().record_error(error_type, endpoint)


# =============================================================================
# CONTEXT MANAGER FOR TIMING
# =============================================================================


class Timer:
    """Context manager for timing operations."""

    def __init__(self, callback: Optional[Callable[[float], None]] = None):
        self.callback = callback
        self.start_time: Optional[float] = None
        self.elapsed_seconds: Optional[float] = None

    def __enter__(self) -> "Timer":
        self.start_time = time.time()
        return self

    def __exit__(self, *args) -> None:
        self.elapsed_seconds = time.time() - self.start_time
        if self.callback:
            self.callback(self.elapsed_seconds)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    # Demo usage
    metrics = get_metrics()

    # Record some sample metrics
    metrics.record_request("/api/v1/knowledge", "GET", 200, 0.045)
    metrics.record_request("/api/v1/knowledge", "POST", 201, 0.123)
    metrics.record_request("/api/v1/knowledge/search", "GET", 200, 0.089)

    metrics.record_search(0.089, 5, faiss_latency_seconds=0.005)
    metrics.record_knowledge_created("database", "lesson_learned")
    metrics.record_mongodb_operation("find", "dakb_knowledge", 0.012)

    metrics.update_faiss_index_metrics(10000, 50000000)
    metrics.update_mongodb_pool_metrics(5, 95)
    metrics.update_agent_count("claude", 3)
    metrics.update_session_count(2)

    # Export Prometheus format
    print("Prometheus Metrics Output:")
    print("=" * 60)
    print(metrics.export_prometheus())
