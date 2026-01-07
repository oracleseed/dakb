"""
DAKB Monitoring Module - Phase 6.3 Production Hardening

Monitoring and observability components for the DAKB system.

Components:
- metrics.py: Prometheus metrics export and collection
- health.py: Health check endpoints and system monitoring

Version: 1.0.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)
"""

from .health import (
    ComponentHealth,
    HealthChecker,
    HealthStatus,
    SystemHealth,
    check_system_health,
)
from .metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsCollector,
    MetricsRegistry,
    get_metrics,
    record_error,
    record_request,
    record_search_latency,
)

__all__ = [
    # Metrics
    "MetricsCollector",
    "MetricsRegistry",
    "Counter",
    "Gauge",
    "Histogram",
    "get_metrics",
    "record_request",
    "record_search_latency",
    "record_error",
    # Health
    "HealthChecker",
    "HealthStatus",
    "ComponentHealth",
    "SystemHealth",
    "check_system_health",
]
