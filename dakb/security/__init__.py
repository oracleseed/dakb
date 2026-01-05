"""
DAKB Security Module - Phase 6.2 Production Hardening

Security utilities and audit tools for the DAKB system.

Components:
- audit.py: Security audit tools and vulnerability detection
- sanitization.py: Input sanitization utilities

Version: 1.0.0
Created: 2025-12-08
Author: Backend Agent (Claude Opus 4.5)
"""

from .audit import (
    SecurityAudit,
    SecurityIssue,
    IssueSeverity,
    IssueStatus,
    run_security_audit,
)

__all__ = [
    "SecurityAudit",
    "SecurityIssue",
    "IssueSeverity",
    "IssueStatus",
    "run_security_audit",
]
