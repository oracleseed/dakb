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

__all__ = ["app", "run"]


def __getattr__(name: str):
    """Lazily expose ``app``/``run`` (PEP 562).

    Importing them lazily avoids eagerly loading ``dakb.gateway.main`` (which
    imports ``dakb.admin``) at package-import time. Eager loading created a
    circular import when ``dakb.admin.*`` was imported before ``dakb.gateway``.
    ``from dakb.gateway import app, run`` still works unchanged.
    """
    if name in ("app", "run"):
        from dakb.gateway.main import app, run

        return {"app": app, "run": run}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
