"""AgenticError exception + FastAPI exception handler.

Raise AgenticError anywhere in API handlers. The global exception handler
catches it and converts to an AgenticEnvelope JSON response with proper HTTP
status, typed issues, remediation guidance, and help links.
"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .envelope import (
    AgenticEnvelope,
    AgenticIssue,
    AgenticRemediation,
    RemediationStep,
)
from .registry import get_issue_registry
from .remediation import get_remediation_engine

logger = logging.getLogger(__name__)


class AgenticError(HTTPException):
    """Raise in handlers to produce a structured agentic error response.

    Extends HTTPException so that FastAPI's default handling still works
    in test clients that don't register agentic exception handlers.
    When the agentic handler IS registered, it takes priority and produces
    the full AgenticEnvelope response.

    Usage::

        raise AgenticError("AUTH.TOKEN_EXPIRED", http_status=401)
        raise AgenticError("VALIDATION.MISSING_PURPOSE", field="purpose", http_status=400)
        raise AgenticError("RATE.LIMIT_EXCEEDED", http_status=429, context={"retry_after": 60})
    """

    def __init__(
        self,
        code: str,
        *,
        field: str | None = None,
        message_override: str | None = None,
        http_status: int = 400,
        context: dict | None = None,
    ):
        self.code = code
        self.field = field
        self.message_override = message_override
        self.http_status = http_status
        self.context = context or {}
        # HTTPException compat: detail is the issue code for fallback rendering
        super().__init__(status_code=http_status, detail=message_override or code)


def register_agentic_handlers(app: FastAPI) -> None:
    """Register global exception handlers that convert errors to AgenticEnvelope."""

    @app.exception_handler(AgenticError)
    async def agentic_error_handler(request: Request, exc: AgenticError):
        registry = get_issue_registry()
        engine = get_remediation_engine()

        issue_def = registry.get(exc.code)
        if issue_def is None:
            # Unknown code — fallback to generic error
            issue = AgenticIssue(
                code=exc.code,
                severity="blocking",
                field=exc.field,
                message=exc.message_override or f"Error: {exc.code}",
                retryable=False,
                category="policy",
                help="/api/help",
            )
            envelope = AgenticEnvelope.error_response(issues=[issue])
            return JSONResponse(
                status_code=exc.http_status,
                content=envelope.to_dict(),
            )

        issue = issue_def.to_issue(
            field=exc.field,
            message_override=exc.message_override,
        )
        remediation = engine.build(issue_def, exc.context)

        envelope = AgenticEnvelope.error_response(
            issues=[issue],
            remediation=remediation,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=envelope.to_dict(),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Convert legacy HTTPException to agentic envelope."""
        # Map HTTP status to issue category
        category_map = {
            400: "validation",
            401: "auth",
            402: "rate",
            403: "policy",
            404: "validation",
            422: "validation",
            429: "rate",
            500: "dependency",
            503: "dependency",
        }
        category = category_map.get(exc.status_code, "policy")
        retryable = exc.status_code in (429, 503)

        issue = AgenticIssue(
            code=f"HTTP.{exc.status_code}",
            severity="blocking",
            field=None,
            message=str(exc.detail) if exc.detail else f"HTTP {exc.status_code}",
            retryable=retryable,
            category=category,
            help="/api/help",
        )

        remediation = None
        if exc.status_code == 429:
            retry_after = None
            if hasattr(exc, "headers") and exc.headers:
                retry_after = exc.headers.get("Retry-After")
            steps = []
            if retry_after:
                steps.append(RemediationStep(
                    type="wait",
                    seconds=int(retry_after),
                ))
            remediation = AgenticRemediation(
                goal="Wait for rate limit reset and retry",
                steps=steps or [RemediationStep(type="wait", seconds=60)],
                retry_endpoint=str(request.url.path),
            )
        elif exc.status_code == 401:
            remediation = AgenticRemediation(
                goal="Re-authenticate with a valid token",
                steps=[RemediationStep(
                    type="action",
                    endpoint="GET /api/onboarding",
                    prompt="Obtain a valid bearer token. See onboarding for auth instructions.",
                )],
            )

        envelope = AgenticEnvelope.error_response(
            issues=[issue],
            remediation=remediation,
        )

        # Preserve original headers (like WWW-Authenticate, Retry-After)
        headers = dict(exc.headers) if exc.headers else None
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope.to_dict(),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        """Convert Pydantic/FastAPI validation errors to agentic issues."""
        issues = []
        for error in exc.errors():
            field_name = str(error["loc"][-1]) if error.get("loc") else None
            issues.append(AgenticIssue(
                code=f"VALIDATION.INVALID_{(field_name or 'INPUT').upper()}",
                severity="blocking",
                field=field_name,
                message=error.get("msg", "Validation error"),
                retryable=True,
                category="validation",
                help=f"/api/help/errors/VALIDATION.INVALID_{(field_name or 'INPUT').upper()}",
            ))

        remediation = AgenticRemediation(
            goal="Fix invalid input fields and retry",
            steps=[
                RemediationStep(
                    type="generate_field",
                    field=i.field,
                    prompt=f"Provide a valid value for '{i.field}': {i.message}",
                )
                for i in issues
                if i.field
            ],
            retry_endpoint=str(request.url.path),
        )

        envelope = AgenticEnvelope.error_response(
            issues=issues,
            remediation=remediation,
        )
        return JSONResponse(
            status_code=422,
            content=envelope.to_dict(),
        )
