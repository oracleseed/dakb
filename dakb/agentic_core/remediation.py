"""Remediation engine — strategy pattern for building fix guidance.

Each issue definition in issues.yaml specifies a remediation_key.
The engine resolves that key to a strategy function that builds
context-aware remediation steps.
"""

from collections.abc import Callable

from .envelope import AgenticRemediation, RemediationStep

RemediationStrategy = Callable[..., AgenticRemediation | None]


# ---------------------------------------------------------------------------
# Built-in strategies
# ---------------------------------------------------------------------------

def missing_fields_strategy(issue_def, context: dict) -> AgenticRemediation:
    """Build remediation for missing/invalid field errors."""
    missing = context.get("missing_fields", [])
    if not missing and issue_def.code:
        # Extract field name from code: VALIDATION.MISSING_PURPOSE -> purpose
        parts = issue_def.code.split(".")
        if len(parts) > 1:
            field_hint = parts[-1].replace("MISSING_", "").replace("INVALID_", "").lower()
            missing = [field_hint]

    steps = []
    for field in missing:
        prompt = context.get(f"{field}_prompt", f"Provide a valid value for '{field}'")
        schema = context.get(f"{field}_schema", {"type": "string"})
        steps.append(RemediationStep(
            type="generate_field",
            field=field,
            prompt=prompt,
            schema=schema,
        ))

    return AgenticRemediation(
        goal=f"Provide missing fields: {', '.join(missing)}" if missing else "Fix validation errors",
        steps=steps or [RemediationStep(type="generate_field", prompt="Fix the invalid input")],
        retry_endpoint=context.get("retry_endpoint"),
    )


def auth_refresh_strategy(issue_def, context: dict) -> AgenticRemediation:
    """Build remediation for auth failures."""
    steps = [
        RemediationStep(
            type="action",
            endpoint="GET /api/onboarding",
            prompt="Check auth instructions in the onboarding endpoint to obtain a valid bearer token.",
        ),
    ]
    if "expired_at" in context:
        steps[0].prompt = (
            f"Token expired at {context['expired_at']}. "
            "Request a new bearer token and retry with the updated Authorization header."
        )
    return AgenticRemediation(
        goal="Re-authenticate with a valid token",
        steps=steps,
        retry_endpoint=context.get("original_endpoint"),
    )


def rate_limit_strategy(issue_def, context: dict) -> AgenticRemediation:
    """Build remediation for rate limit errors."""
    retry_after = context.get("retry_after", 60)
    steps = [
        RemediationStep(type="wait", seconds=retry_after),
    ]
    alternatives = context.get("alternatives", [])
    if alternatives:
        steps.append(RemediationStep(type="alternatives", options=alternatives))

    return AgenticRemediation(
        goal=f"Wait {retry_after}s for rate limit reset, then retry",
        steps=steps,
        retry_endpoint=context.get("retry_endpoint"),
    )


def service_not_found_strategy(issue_def, context: dict) -> AgenticRemediation:
    """Build remediation when a service or endpoint is not found."""
    return AgenticRemediation(
        goal="Find the correct service or endpoint",
        steps=[
            RemediationStep(
                type="action",
                endpoint="GET /api/help",
                prompt="List available services and their endpoints via the help API.",
            ),
        ],
    )


def billing_exhausted_strategy(issue_def, context: dict) -> AgenticRemediation:
    """Build remediation for billing cap errors."""
    return AgenticRemediation(
        goal="Resolve billing cap and retry",
        steps=[
            RemediationStep(
                type="action",
                endpoint="GET /admin/api/usage",
                prompt=f"Check current usage status. Current spend: ${context.get('current_spend', '?')} / ${context.get('cap', '?')}.",
            ),
            RemediationStep(
                type="alternatives",
                options=[
                    "Wait for billing period reset",
                    "Contact admin to increase billing cap",
                    "Use a different service with available budget",
                ],
            ),
        ],
    )


def credential_error_strategy(issue_def, context: dict) -> AgenticRemediation:
    """Build remediation for credential configuration errors."""
    return AgenticRemediation(
        goal="Configure credentials for this service",
        steps=[
            RemediationStep(
                type="action",
                endpoint="POST /admin/api/credentials",
                prompt="Create or update credentials for this service. Requires admin role.",
            ),
        ],
    )


def role_insufficient_strategy(issue_def, context: dict) -> AgenticRemediation:
    """Build remediation for insufficient role permissions."""
    required = context.get("required_roles", [])
    return AgenticRemediation(
        goal="Obtain required role permissions",
        steps=[
            RemediationStep(
                type="action",
                endpoint="GET /admin/api/roles",
                prompt=(
                    f"Required role(s): {', '.join(required) if required else 'unknown'}. "
                    "Check available roles and request the appropriate role assignment from an admin."
                ),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES: dict[str, RemediationStrategy] = {
    "missing_fields": missing_fields_strategy,
    "auth_refresh": auth_refresh_strategy,
    "rate_limit": rate_limit_strategy,
    "service_not_found": service_not_found_strategy,
    "billing_exhausted": billing_exhausted_strategy,
    "credential_error": credential_error_strategy,
    "role_insufficient": role_insufficient_strategy,
}


class RemediationEngine:
    """Resolves remediation strategies by key from issue definitions."""

    def __init__(self, strategies: dict[str, RemediationStrategy] | None = None):
        self._strategies = {**STRATEGIES, **(strategies or {})}

    def build(self, issue_def, context: dict) -> AgenticRemediation | None:
        if not issue_def.remediation_key:
            return None
        strategy = self._strategies.get(issue_def.remediation_key)
        if not strategy:
            return None
        return strategy(issue_def, context)

    def register(self, key: str, strategy: RemediationStrategy):
        self._strategies[key] = strategy


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: RemediationEngine | None = None


def get_remediation_engine() -> RemediationEngine:
    global _engine
    if _engine is None:
        _engine = RemediationEngine()
    return _engine
