"""
DAKB Gateway Service
====================

FastAPI-based REST API gateway for DAKB operations.

Components:
- main.py: FastAPI application and lifespan management
- config.py: Configuration loading and validation
- routes/: API endpoint handlers
- middleware/: Authentication, CORS, rate limiting

Usage:
    # Start the gateway
    python -m dakb.gateway

    # Or import for programmatic use
    from dakb.gateway.main import app
"""

from dakb.gateway.main import app, run

__all__ = ["app", "run"]
