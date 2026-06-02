"""
DAKB Integration Module

Provides integration utilities for connecting different DAKB subsystems.
Enables seamless coordination between agent registration, alias management,
and other core services.

Modules:
- alias_registration: Integration between agent self-registration and alias system
"""

from .alias_registration import (
    AgentRegistrationResult,
    AliasConflictError,
    register_agent_with_alias,
)

__all__ = [
    "register_agent_with_alias",
    "AliasConflictError",
    "AgentRegistrationResult",
]
