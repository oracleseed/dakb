"""
DAKB Database Repositories Package

This package organizes repository classes by feature area.

Modules:
- registration: Invite token and registration audit repositories (Self-Registration v1.0)
"""

# Lazy imports to avoid circular import issues
def get_registration_repositories():
    """
    Get registration repository classes.

    Uses lazy import pattern to avoid circular imports during module initialization.
    """
    from .registration import InviteTokenRepository, RegistrationAuditRepository
    return InviteTokenRepository, RegistrationAuditRepository


# Direct imports for convenience (these work after all modules are loaded)
try:
    from .registration import (
        InviteTokenRepository,
        RegistrationAuditRepository,
    )
except ImportError:
    # Handle case where imports fail during initial load
    InviteTokenRepository = None
    RegistrationAuditRepository = None


__all__ = [
    "InviteTokenRepository",
    "RegistrationAuditRepository",
    "get_registration_repositories",
]
