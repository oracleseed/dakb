"""Agentic Response Envelope — Pydantic models for self-adaptive API responses.

Every response wraps data in an AgenticEnvelope that includes:
- Typed issues with remediation guidance
- Available next actions with safety labels
- Constraints the agent must respect
- Help links for self-documentation
- Observability metadata (trace_id, schema_version)
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class RemediationStep(BaseModel):
    """One step in a remediation action plan."""
    type: Literal["generate_field", "action", "wait", "alternatives"]
    field: str | None = None
    prompt: str | None = None
    schema_def: dict | None = Field(None, alias="schema")
    endpoint: str | None = None
    seconds: int | None = None
    options: list[str] | None = None

    model_config = {"populate_by_name": True}


class AgenticIssue(BaseModel):
    """A typed, structured problem in the response."""
    code: str
    severity: Literal["blocking", "warning", "info"]
    field: str | None = None
    message: str
    retryable: bool
    category: Literal["validation", "auth", "rate", "policy", "dependency"]
    help: str


class AgenticAction(BaseModel):
    """A contextual next step the agent can take."""
    name: str
    method: str
    endpoint: str
    safe: bool
    idempotent: bool
    risk: Literal["none", "low", "high", "destructive"]
    requires_confirmation: bool = False
    parameters_schema: dict = Field(default_factory=dict)
    help: str


class AgenticRemediation(BaseModel):
    """Ordered action plan for self-correction."""
    goal: str
    steps: list[RemediationStep]
    hold_id: str | None = None
    retry_endpoint: str | None = None


class AgenticEnvelope(BaseModel):
    """Standard agentic API response wrapper."""
    status: str
    data: Any | None = None
    issues: list[AgenticIssue] = Field(default_factory=list)
    remediation: AgenticRemediation | None = None
    available_actions: list[AgenticAction] = Field(default_factory=list)
    constraints: dict = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)
    help: str = "/api/help"
    meta: dict = Field(default_factory=dict)

    @classmethod
    def success(
        cls,
        data: Any,
        status: str = "ok",
        **kwargs,
    ) -> "AgenticEnvelope":
        return cls(status=status, data=data, meta=build_meta(), **kwargs)

    @classmethod
    def created(cls, data: Any, **kwargs) -> "AgenticEnvelope":
        return cls(status="created", data=data, meta=build_meta(), **kwargs)

    @classmethod
    def deleted(cls, data: Any, **kwargs) -> "AgenticEnvelope":
        return cls(status="deleted", data=data, meta=build_meta(), **kwargs)

    @classmethod
    def error_response(
        cls,
        issues: list[AgenticIssue],
        remediation: AgenticRemediation | None = None,
        **kwargs,
    ) -> "AgenticEnvelope":
        status = _category_to_status(issues[0].category) if issues else "error"
        return cls(
            status=status,
            issues=issues,
            remediation=remediation,
            meta=build_meta(),
            **kwargs,
        )

    def to_dict(self) -> dict:
        return self.model_dump(exclude_none=True, by_alias=True)


class ValidationResult(BaseModel):
    """Result of adaptive validation — NOT an envelope."""
    ok: bool
    issues: list[AgenticIssue] = Field(default_factory=list)
    normalized_data: dict | None = None
    remediation: AgenticRemediation | None = None

    @classmethod
    def valid(cls, data: dict | None = None) -> "ValidationResult":
        return cls(ok=True, normalized_data=data)

    @classmethod
    def invalid(
        cls,
        issues: list[AgenticIssue],
        remediation: AgenticRemediation | None = None,
    ) -> "ValidationResult":
        return cls(ok=False, issues=issues, remediation=remediation)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_meta(extra: dict | None = None) -> dict:
    meta = {
        "trace_id": f"tr_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.0",
        "api_version": "v1",
    }
    if extra:
        meta.update(extra)
    return meta


def _category_to_status(category: str) -> str:
    return {
        "validation": "content_guidance",
        "auth": "unauthorized",
        "rate": "rate_limited",
        "policy": "forbidden",
        "dependency": "error",
    }.get(category, "error")
