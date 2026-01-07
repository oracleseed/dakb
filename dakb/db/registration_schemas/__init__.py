"""
DAKB Registration Schemas Package

Self-contained Pydantic schemas for invite-only agent registration system.

Modules:
- registration: Invite-only agent registration schemas (v1.0)

Note: This package is named registration_schemas to avoid shadowing the
parent db/schemas.py file. The registration module defines its own local
copies of AgentType, AgentRole, and AccessLevel to avoid circular imports.
"""

from .registration import (
    # Local enums (copies to avoid circular import)
    AgentType as RegAgentType,
    AgentRole as RegAgentRole,
    AccessLevel as RegAccessLevel,
    # Registration-specific enums
    InviteTokenStatus,
    RegistrationAuditAction,
    # Main schemas
    DakbInviteToken,
    DakbRegistrationAudit,
    # Create/Update schemas
    InviteTokenCreate,
    RegistrationRequest,
    # Response models
    InviteTokenResponse,
    RegistrationResponse,
    RegistrationErrorResponse,
    RevocationResponse,
    AuditListResponse,
    TokenListResponse,
    # Helper functions
    generate_invite_token,
    generate_audit_id,
)

__all__ = [
    # Local enums (aliased)
    "RegAgentType",
    "RegAgentRole",
    "RegAccessLevel",
    # Registration-specific enums
    "InviteTokenStatus",
    "RegistrationAuditAction",
    # Main schemas
    "DakbInviteToken",
    "DakbRegistrationAudit",
    # Create/Update schemas
    "InviteTokenCreate",
    "RegistrationRequest",
    # Response models
    "InviteTokenResponse",
    "RegistrationResponse",
    "RegistrationErrorResponse",
    "RevocationResponse",
    "AuditListResponse",
    "TokenListResponse",
    # Helper functions
    "generate_invite_token",
    "generate_audit_id",
]
