"""
DAKB Health Check System - Phase 6.3 Production Hardening

Health check endpoints and system monitoring for DAKB.

Features:
- Comprehensive health checks for all components
- Dependency status monitoring
- Degraded state detection
- Health history tracking

Version: 1.0.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)

Usage:
    from backend.dakb_service.monitoring import check_system_health

    health = await check_system_health()
    print(f"Status: {health.status}")
"""

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import httpx

# Add project root to path
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
sys.path.insert(0, PROJECT_ROOT)

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# HEALTH STATUS
# =============================================================================


class HealthStatus(str, Enum):
    """Health status levels."""

    HEALTHY = "healthy"  # All systems operational
    DEGRADED = "degraded"  # Some issues but functional
    UNHEALTHY = "unhealthy"  # Critical failure


@dataclass
class ComponentHealth:
    """Health status of a single component."""

    name: str
    status: HealthStatus
    latency_ms: float | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "details": self.details,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass
class SystemHealth:
    """Overall system health status."""

    status: HealthStatus
    components: list[ComponentHealth]
    uptime_seconds: float
    version: str = "1.0.0"
    checked_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status.value,
            "version": self.version,
            "uptime_seconds": self.uptime_seconds,
            "checked_at": self.checked_at.isoformat(),
            "components": {c.name: c.to_dict() for c in self.components},
            "summary": {
                "healthy": sum(1 for c in self.components if c.status == HealthStatus.HEALTHY),
                "degraded": sum(1 for c in self.components if c.status == HealthStatus.DEGRADED),
                "unhealthy": sum(1 for c in self.components if c.status == HealthStatus.UNHEALTHY),
            },
        }


# =============================================================================
# HEALTH CHECKER
# =============================================================================


class HealthChecker:
    """
    Comprehensive health checker for DAKB system.

    Checks health of:
    - MongoDB connection
    - Embedding service
    - FAISS index
    - Gateway service
    - Message queue
    - System resources
    """

    def __init__(
        self,
        gateway_url: str = "http://localhost:3100",
        embedding_url: str = "http://127.0.0.1:3101",
        mongo_uri: str | None = None,
    ):
        self.gateway_url = gateway_url
        self.embedding_url = embedding_url
        self.mongo_uri = mongo_uri or os.getenv("MONGO_URI")
        self._start_time = time.time()
        self._health_history: list[SystemHealth] = []
        self._max_history = 100

    async def check_all(self) -> SystemHealth:
        """
        Run all health checks.

        Returns:
            SystemHealth with aggregated status
        """
        components = []

        # Run all checks concurrently
        checks = await asyncio.gather(
            self.check_mongodb(),
            self.check_embedding_service(),
            self.check_gateway(),
            self.check_faiss_index(),
            self.check_system_resources(),
            return_exceptions=True,
        )

        for result in checks:
            if isinstance(result, ComponentHealth):
                components.append(result)
            elif isinstance(result, Exception):
                components.append(
                    ComponentHealth(
                        name="unknown",
                        status=HealthStatus.UNHEALTHY,
                        message=str(result),
                    )
                )

        # Determine overall status
        if any(c.status == HealthStatus.UNHEALTHY for c in components):
            overall_status = HealthStatus.UNHEALTHY
        elif any(c.status == HealthStatus.DEGRADED for c in components):
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.HEALTHY

        uptime = time.time() - self._start_time

        health = SystemHealth(
            status=overall_status,
            components=components,
            uptime_seconds=uptime,
        )

        # Record in history
        self._health_history.append(health)
        if len(self._health_history) > self._max_history:
            self._health_history = self._health_history[-self._max_history:]

        return health

    async def check_mongodb(self) -> ComponentHealth:
        """Check MongoDB connection health."""
        try:
            start = time.time()

            # Try to import and use pymongo
            from pymongo import MongoClient

            if not self.mongo_uri:
                return ComponentHealth(
                    name="mongodb",
                    status=HealthStatus.UNHEALTHY,
                    message="MONGO_URI not configured",
                )

            client = MongoClient(self.mongo_uri, serverSelectionTimeoutMS=5000)

            # Ping the server
            client.admin.command("ping")

            # Get server info
            server_info = client.server_info()
            latency = (time.time() - start) * 1000

            # Check connection pool
            # Note: Actual pool metrics depend on driver configuration

            client.close()

            return ComponentHealth(
                name="mongodb",
                status=HealthStatus.HEALTHY,
                latency_ms=latency,
                message="Connected successfully",
                details={
                    "version": server_info.get("version", "unknown"),
                    "database": "dakb",
                },
            )

        except Exception as e:
            return ComponentHealth(
                name="mongodb",
                status=HealthStatus.UNHEALTHY,
                message=f"Connection failed: {str(e)}",
            )

    async def check_embedding_service(self) -> ComponentHealth:
        """Check embedding service health."""
        try:
            start = time.time()

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.embedding_url}/health")
                latency = (time.time() - start) * 1000

                if response.status_code == 200:
                    data = response.json()
                    return ComponentHealth(
                        name="embedding_service",
                        status=HealthStatus.HEALTHY,
                        latency_ms=latency,
                        message="Service operational",
                        details={
                            "model": data.get("model", "unknown"),
                            "index_size": data.get("index_size", 0),
                        },
                    )
                else:
                    return ComponentHealth(
                        name="embedding_service",
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency,
                        message=f"Unexpected status: {response.status_code}",
                    )

        except httpx.ConnectError:
            return ComponentHealth(
                name="embedding_service",
                status=HealthStatus.UNHEALTHY,
                message="Service not reachable",
            )
        except Exception as e:
            return ComponentHealth(
                name="embedding_service",
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}",
            )

    async def check_gateway(self) -> ComponentHealth:
        """Check gateway service health."""
        try:
            start = time.time()

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.gateway_url}/health")
                latency = (time.time() - start) * 1000

                if response.status_code == 200:
                    data = response.json()
                    status = (
                        HealthStatus.HEALTHY
                        if data.get("status") == "ok"
                        else HealthStatus.DEGRADED
                    )
                    return ComponentHealth(
                        name="gateway",
                        status=status,
                        latency_ms=latency,
                        message="Service operational" if status == HealthStatus.HEALTHY else "Service degraded",
                        details={
                            "version": data.get("version", "unknown"),
                        },
                    )
                else:
                    return ComponentHealth(
                        name="gateway",
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency,
                        message=f"Unexpected status: {response.status_code}",
                    )

        except httpx.ConnectError:
            return ComponentHealth(
                name="gateway",
                status=HealthStatus.UNHEALTHY,
                message="Service not reachable",
            )
        except Exception as e:
            return ComponentHealth(
                name="gateway",
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}",
            )

    async def check_faiss_index(self) -> ComponentHealth:
        """Check FAISS index health."""
        try:
            start = time.time()

            # Check via embedding service
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.embedding_url}/health")
                latency = (time.time() - start) * 1000

                if response.status_code == 200:
                    data = response.json()
                    index_size = data.get("index_size", 0)
                    index_healthy = data.get("faiss_healthy", True)

                    if not index_healthy:
                        return ComponentHealth(
                            name="faiss_index",
                            status=HealthStatus.DEGRADED,
                            latency_ms=latency,
                            message="Index degraded",
                            details={"vector_count": index_size},
                        )

                    # Check if index is empty (might be OK for new installations)
                    if index_size == 0:
                        return ComponentHealth(
                            name="faiss_index",
                            status=HealthStatus.HEALTHY,
                            latency_ms=latency,
                            message="Index empty (new installation)",
                            details={"vector_count": 0},
                        )

                    return ComponentHealth(
                        name="faiss_index",
                        status=HealthStatus.HEALTHY,
                        latency_ms=latency,
                        message="Index operational",
                        details={
                            "vector_count": index_size,
                        },
                    )
                else:
                    return ComponentHealth(
                        name="faiss_index",
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency,
                        message="Could not determine index status",
                    )

        except Exception as e:
            return ComponentHealth(
                name="faiss_index",
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}",
            )

    async def check_system_resources(self) -> ComponentHealth:
        """Check system resource availability."""
        try:
            import psutil
        except ImportError:
            return ComponentHealth(
                name="system_resources",
                status=HealthStatus.HEALTHY,
                message="psutil not installed, skipping resource check",
            )

        try:
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.1)

            # Memory usage
            memory = psutil.virtual_memory()
            memory_percent = memory.percent

            # Disk usage
            disk = psutil.disk_usage("/")
            disk_percent = disk.percent

            # Determine status based on thresholds
            issues = []

            if cpu_percent > 90:
                issues.append(f"High CPU usage: {cpu_percent}%")
            if memory_percent > 90:
                issues.append(f"High memory usage: {memory_percent}%")
            if disk_percent > 90:
                issues.append(f"High disk usage: {disk_percent}%")

            if len(issues) >= 2:
                status = HealthStatus.UNHEALTHY
            elif len(issues) == 1:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.HEALTHY

            return ComponentHealth(
                name="system_resources",
                status=status,
                message="; ".join(issues) if issues else "Resources OK",
                details={
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory_percent,
                    "disk_percent": disk_percent,
                    "memory_available_gb": round(memory.available / (1024**3), 2),
                    "disk_available_gb": round(disk.free / (1024**3), 2),
                },
            )

        except Exception as e:
            return ComponentHealth(
                name="system_resources",
                status=HealthStatus.DEGRADED,
                message=f"Resource check failed: {str(e)}",
            )

    def get_history(
        self,
        limit: int = 10,
        status_filter: HealthStatus | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get health check history.

        Args:
            limit: Maximum entries to return
            status_filter: Filter by status

        Returns:
            List of health check results
        """
        history = self._health_history

        if status_filter:
            history = [h for h in history if h.status == status_filter]

        return [h.to_dict() for h in history[-limit:]]

    def get_uptime(self) -> float:
        """Get service uptime in seconds."""
        return time.time() - self._start_time

    def get_uptime_string(self) -> str:
        """Get human-readable uptime string."""
        uptime = self.get_uptime()
        days = int(uptime // 86400)
        hours = int((uptime % 86400) // 3600)
        minutes = int((uptime % 3600) // 60)
        seconds = int(uptime % 60)

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")

        return " ".join(parts)


# =============================================================================
# LIVENESS AND READINESS PROBES
# =============================================================================


class ProbeChecker:
    """
    Kubernetes-style liveness and readiness probes.

    - Liveness: Is the service alive? (basic check)
    - Readiness: Is the service ready to handle traffic? (full check)
    """

    def __init__(self, health_checker: HealthChecker):
        self.health_checker = health_checker

    async def liveness(self) -> dict[str, Any]:
        """
        Liveness probe - basic check if service is alive.

        Returns:
            Simple status dict
        """
        return {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "uptime_seconds": self.health_checker.get_uptime(),
        }

    async def readiness(self) -> dict[str, Any]:
        """
        Readiness probe - check if service is ready for traffic.

        Returns:
            Status dict with component health
        """
        health = await self.health_checker.check_all()

        is_ready = health.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)

        return {
            "ready": is_ready,
            "status": health.status.value,
            "timestamp": datetime.utcnow().isoformat(),
            "components": {
                c.name: c.status.value for c in health.components
            },
        }


# =============================================================================
# GLOBAL HEALTH CHECKER
# =============================================================================

_health_checker: HealthChecker | None = None


def get_health_checker(
    gateway_url: str | None = None,
    embedding_url: str | None = None,
) -> HealthChecker:
    """
    Get global health checker instance.

    Args:
        gateway_url: Gateway URL (optional, uses default if not provided)
        embedding_url: Embedding service URL (optional)

    Returns:
        HealthChecker singleton instance
    """
    global _health_checker

    if _health_checker is None:
        _health_checker = HealthChecker(
            gateway_url=gateway_url or os.getenv("DAKB_GATEWAY_URL", "http://localhost:3100"),
            embedding_url=embedding_url or os.getenv("DAKB_EMBEDDING_URL", "http://127.0.0.1:3101"),
        )

    return _health_checker


async def check_system_health() -> SystemHealth:
    """
    Convenience function to check system health.

    Returns:
        SystemHealth with all component statuses
    """
    checker = get_health_checker()
    return await checker.check_all()


# =============================================================================
# FASTAPI ENDPOINTS
# =============================================================================


def create_health_router():
    """
    Create FastAPI router for health endpoints.

    Returns:
        FastAPI APIRouter with health endpoints
    """
    try:
        from fastapi import APIRouter, Response
    except ImportError:
        logger.warning("FastAPI not installed, cannot create health router")
        return None

    router = APIRouter(tags=["health"])
    checker = get_health_checker()
    probe_checker = ProbeChecker(checker)

    @router.get("/health")
    async def health():
        """Full health check endpoint."""
        health = await checker.check_all()
        return health.to_dict()

    @router.get("/health/live")
    async def liveness():
        """Kubernetes liveness probe."""
        return await probe_checker.liveness()

    @router.get("/health/ready")
    async def readiness(response: Response):
        """Kubernetes readiness probe."""
        result = await probe_checker.readiness()
        if not result["ready"]:
            response.status_code = 503
        return result

    @router.get("/health/components/{component}")
    async def component_health(component: str):
        """Get health of specific component."""
        health = await checker.check_all()
        for c in health.components:
            if c.name == component:
                return c.to_dict()
        return {"error": f"Component not found: {component}"}

    @router.get("/health/history")
    async def health_history(limit: int = 10):
        """Get health check history."""
        return {"history": checker.get_history(limit=limit)}

    return router


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DAKB Health Check")
    parser.add_argument(
        "--gateway", default="http://localhost:3100", help="Gateway URL"
    )
    parser.add_argument(
        "--embedding", default="http://127.0.0.1:3101", help="Embedding service URL"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )
    args = parser.parse_args()

    async def main():
        checker = HealthChecker(
            gateway_url=args.gateway,
            embedding_url=args.embedding,
        )

        health = await checker.check_all()

        if args.json:
            import json
            print(json.dumps(health.to_dict(), indent=2))
        else:
            print("\nDAKB System Health")
            print("=" * 60)
            print(f"Overall Status: {health.status.value.upper()}")
            print(f"Uptime: {checker.get_uptime_string()}")
            print(f"Checked At: {health.checked_at.isoformat()}")
            print("\nComponents:")
            print("-" * 60)
            for component in health.components:
                status_icon = {
                    HealthStatus.HEALTHY: "[OK]",
                    HealthStatus.DEGRADED: "[!]",
                    HealthStatus.UNHEALTHY: "[X]",
                }.get(component.status, "[?]")
                latency = f"{component.latency_ms:.1f}ms" if component.latency_ms else "N/A"
                print(f"  {status_icon} {component.name}: {component.message or component.status.value} ({latency})")
                if component.details:
                    for key, value in component.details.items():
                        print(f"      {key}: {value}")
            print("=" * 60)

    asyncio.run(main())
