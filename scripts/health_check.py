#!/usr/bin/env python3
"""
DAKB Health Check Script

Performs comprehensive health checks on DAKB services.
Returns exit code 0 if healthy, 1 if unhealthy.

Usage:
    python health_check.py
    python health_check.py --verbose
    python health_check.py --json

Version: 1.0.0
Created: 2025-12-17
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Run: pip install httpx")
    sys.exit(1)


class HealthChecker:
    """
    Health checker for DAKB services.

    Checks:
    - Gateway availability
    - Embedding service availability
    - MongoDB connectivity (via gateway)
    - MCP endpoint responsiveness
    """

    def __init__(
        self,
        gateway_url: str = "http://localhost:3100",
        embedding_url: str = "http://localhost:3101",
        timeout: float = 10.0,
    ):
        self.gateway_url = gateway_url.rstrip("/")
        self.embedding_url = embedding_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout)

    def check_gateway(self) -> dict[str, Any]:
        """Check gateway health."""
        start = time.time()
        try:
            response = self.client.get(f"{self.gateway_url}/health")
            latency_ms = (time.time() - start) * 1000

            if response.status_code == 200:
                data = response.json()
                return {
                    "service": "gateway",
                    "status": "healthy",
                    "latency_ms": round(latency_ms, 2),
                    "details": data,
                }
            else:
                return {
                    "service": "gateway",
                    "status": "unhealthy",
                    "latency_ms": round(latency_ms, 2),
                    "error": f"Status code: {response.status_code}",
                }
        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return {
                "service": "gateway",
                "status": "unhealthy",
                "latency_ms": round(latency_ms, 2),
                "error": str(e),
            }

    def check_embedding(self) -> dict[str, Any]:
        """Check embedding service health."""
        start = time.time()
        try:
            response = self.client.get(f"{self.embedding_url}/health")
            latency_ms = (time.time() - start) * 1000

            if response.status_code == 200:
                return {
                    "service": "embedding",
                    "status": "healthy",
                    "latency_ms": round(latency_ms, 2),
                }
            else:
                return {
                    "service": "embedding",
                    "status": "unhealthy",
                    "latency_ms": round(latency_ms, 2),
                    "error": f"Status code: {response.status_code}",
                }
        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return {
                "service": "embedding",
                "status": "unhealthy",
                "latency_ms": round(latency_ms, 2),
                "error": str(e),
            }

    def check_mcp_endpoint(self, token: str = None) -> dict[str, Any]:
        """Check MCP endpoint health."""
        start = time.time()
        try:
            # Send a simple ping request
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            request_body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "ping",
            }

            response = self.client.post(
                f"{self.gateway_url}/mcp",
                json=request_body,
                headers=headers,
            )
            latency_ms = (time.time() - start) * 1000

            if response.status_code == 200:
                data = response.json()
                if data.get("result", {}).get("pong"):
                    return {
                        "service": "mcp",
                        "status": "healthy",
                        "latency_ms": round(latency_ms, 2),
                    }

            return {
                "service": "mcp",
                "status": "degraded",
                "latency_ms": round(latency_ms, 2),
                "note": "Endpoint reachable but ping failed (may need auth)",
            }
        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return {
                "service": "mcp",
                "status": "unhealthy",
                "latency_ms": round(latency_ms, 2),
                "error": str(e),
            }

    def check_mongodb(self) -> dict[str, Any]:
        """Check MongoDB connectivity via gateway status."""
        start = time.time()
        try:
            response = self.client.get(f"{self.gateway_url}/api/v1/status")
            latency_ms = (time.time() - start) * 1000

            if response.status_code == 200:
                data = response.json()
                mongodb_status = data.get("data", {}).get("mongodb_status", "unknown")

                if mongodb_status == "connected":
                    return {
                        "service": "mongodb",
                        "status": "healthy",
                        "latency_ms": round(latency_ms, 2),
                    }
                else:
                    return {
                        "service": "mongodb",
                        "status": "unhealthy",
                        "latency_ms": round(latency_ms, 2),
                        "error": f"MongoDB status: {mongodb_status}",
                    }
            else:
                return {
                    "service": "mongodb",
                    "status": "unknown",
                    "latency_ms": round(latency_ms, 2),
                    "error": "Could not determine MongoDB status",
                }
        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return {
                "service": "mongodb",
                "status": "unknown",
                "latency_ms": round(latency_ms, 2),
                "error": str(e),
            }

    def run_all_checks(self, token: str = None) -> dict[str, Any]:
        """
        Run all health checks.

        Returns:
            Complete health report
        """
        checks = [
            self.check_gateway(),
            self.check_embedding(),
            self.check_mcp_endpoint(token),
            self.check_mongodb(),
        ]

        # Determine overall status
        statuses = [c["status"] for c in checks]
        if all(s == "healthy" for s in statuses):
            overall = "healthy"
        elif any(s == "unhealthy" for s in statuses):
            overall = "unhealthy"
        else:
            overall = "degraded"

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "overall_status": overall,
            "gateway_url": self.gateway_url,
            "embedding_url": self.embedding_url,
            "checks": checks,
        }

    def close(self):
        """Close HTTP client."""
        self.client.close()


def main():
    parser = argparse.ArgumentParser(
        description="DAKB Health Check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--gateway",
        default=os.getenv("DAKB_GATEWAY_URL", "http://localhost:3100"),
        help="Gateway URL",
    )
    parser.add_argument(
        "--embedding",
        default=os.getenv("DAKB_EMBEDDING_URL", "http://localhost:3101"),
        help="Embedding service URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("DAKB_AUTH_TOKEN"),
        help="Auth token for MCP check",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Request timeout in seconds",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    checker = HealthChecker(
        gateway_url=args.gateway,
        embedding_url=args.embedding,
        timeout=args.timeout,
    )

    try:
        result = checker.run_all_checks(args.token)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            # Human-readable output
            print(f"DAKB Health Check - {result['timestamp']}")
            print("=" * 50)
            print(f"Overall Status: {result['overall_status'].upper()}")
            print()

            for check in result["checks"]:
                status_icon = "✓" if check["status"] == "healthy" else "✗"
                print(f"  {status_icon} {check['service']}: {check['status']} ({check['latency_ms']}ms)")

                if args.verbose:
                    if "error" in check:
                        print(f"      Error: {check['error']}")
                    if "details" in check:
                        print(f"      Details: {check['details']}")
                    if "note" in check:
                        print(f"      Note: {check['note']}")

            print()

        # Exit code based on overall status
        if result["overall_status"] == "unhealthy":
            sys.exit(1)
        else:
            sys.exit(0)

    finally:
        checker.close()


if __name__ == "__main__":
    main()
