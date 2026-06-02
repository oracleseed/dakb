"""Agentic API core — reusable self-adaptive response contract.

This package provides the framework-agnostic building blocks for an
agentic API: typed response envelopes, config-driven issue/action
registries, a remediation strategy engine, and a FastAPI exception
handler that converts errors into structured agentic responses.

It is intentionally domain-neutral. Domain-specific issue codes,
actions, and remediation strategies are layered on top by callers
(see :mod:`dakb.gateway.agentic`).

Typical usage::

    from dakb.agentic_core import AgenticEnvelope, AgenticError

    @app.get("/thing")
    async def get_thing():
        return AgenticEnvelope.success(data={...}).to_dict()
"""

from .envelope import (
    AgenticAction,
    AgenticEnvelope,
    AgenticIssue,
    AgenticRemediation,
    RemediationStep,
    ValidationResult,
    build_meta,
)
from .exceptions import AgenticError, register_agentic_handlers
from .registry import (
    ActionDefinition,
    ActionRegistry,
    IssueDefinition,
    IssueRegistry,
    get_action_registry,
    get_issue_registry,
)
from .remediation import (
    RemediationEngine,
    RemediationStrategy,
    get_remediation_engine,
)

__all__ = [
    # envelope
    "AgenticEnvelope",
    "AgenticIssue",
    "AgenticAction",
    "AgenticRemediation",
    "RemediationStep",
    "ValidationResult",
    "build_meta",
    # registry
    "IssueDefinition",
    "IssueRegistry",
    "ActionDefinition",
    "ActionRegistry",
    "get_issue_registry",
    "get_action_registry",
    # remediation
    "RemediationEngine",
    "RemediationStrategy",
    "get_remediation_engine",
    # exceptions
    "AgenticError",
    "register_agentic_handlers",
]
