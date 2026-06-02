"""DAKB Gateway Agentic API package.

Reuses the typed Pydantic envelope models from :mod:`dakb.agentic_core`
but layers on DAKB-specific issue codes, actions, and remediation
strategies (knowledge CRUD, search, moderation, sessions, messaging, vault).

Usage in DAKB route handlers::

    from dakb.gateway.agentic import raise_issue, ok_response

    # Error with full remediation
    raise_issue("KB.NOT_FOUND", status=404, context={"knowledge_id": kid})

    # Success with actions
    return ok_response(data={...}, actions=["search_knowledge", "store_knowledge"])
"""

import logging
from pathlib import Path

from dakb.agentic_core.envelope import (
    AgenticAction,
    AgenticEnvelope,
    AgenticIssue,
    AgenticRemediation,
    RemediationStep,
    ValidationResult,
    _category_to_status,
    build_meta,
)
from dakb.agentic_core.exceptions import AgenticError
from dakb.agentic_core.registry import (
    ActionRegistry,
    IssueRegistry,
)
from dakb.agentic_core.remediation import RemediationEngine

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent / "config"

# ---------------------------------------------------------------------------
# DAKB-specific registries
# ---------------------------------------------------------------------------

_dakb_issues: IssueRegistry | None = None
_dakb_actions: ActionRegistry | None = None
_dakb_engine: RemediationEngine | None = None


def get_dakb_issue_registry() -> IssueRegistry:
    global _dakb_issues
    if _dakb_issues is None:
        _dakb_issues = IssueRegistry(CONFIG_DIR / "issues.yaml")
    return _dakb_issues


def get_dakb_action_registry() -> ActionRegistry:
    global _dakb_actions
    if _dakb_actions is None:
        _dakb_actions = ActionRegistry(CONFIG_DIR / "actions.yaml")
    return _dakb_actions


def get_dakb_remediation_engine() -> RemediationEngine:
    global _dakb_engine
    if _dakb_engine is None:
        from .remediation import DAKB_STRATEGIES
        _dakb_engine = RemediationEngine(strategies=DAKB_STRATEGIES)
    return _dakb_engine


# ---------------------------------------------------------------------------
# Helpers for route handlers
# ---------------------------------------------------------------------------

def raise_issue(
    code: str,
    *,
    status: int = 400,
    field: str | None = None,
    message: str | None = None,
    context: dict | None = None,
):
    """Raise an AgenticError caught by the registered exception handler."""
    raise AgenticError(
        code=code,
        field=field,
        message_override=message,
        http_status=status,
        context=context or {},
    )


def ok_response(
    data,
    status: str = "ok",
    http_status: int = 200,
    actions: list[str] | None = None,
    constraints: dict | None = None,
    suggestions: list[str] | None = None,
):
    """Build a success AgenticEnvelope as a JSONResponse.

    Returns JSONResponse directly to bypass FastAPI's response_model
    validation (agentic envelope has different shape than Pydantic models).
    """
    import json as _json
    from datetime import datetime as _dt

    from fastapi.responses import JSONResponse

    class _Encoder(_json.JSONEncoder):
        def default(self, o):
            if isinstance(o, _dt):
                return o.isoformat()
            return super().default(o)

    action_objs = []
    if actions:
        action_objs = get_dakb_action_registry().get_actions_for(*actions)

    envelope = AgenticEnvelope.success(
        data=data,
        status=status,
        available_actions=action_objs,
        constraints=constraints or {},
        suggestions=suggestions or [],
    )
    # Use custom encoder to handle datetime objects in data
    body = _json.dumps(envelope.to_dict(), cls=_Encoder)
    return JSONResponse(content=_json.loads(body), status_code=http_status)


# ---------------------------------------------------------------------------
# Wire into FastAPI app
# ---------------------------------------------------------------------------

def register_dakb_agentic_handlers(app):
    """Register agentic exception handlers on the DAKB FastAPI app.

    Creates handlers that use DAKB registries (not the core defaults).
    Call in main.py after app creation.
    """
    import json as _json

    from fastapi import HTTPException as FastAPIHTTPException
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    # Use Starlette's base HTTPException to catch ALL HTTP errors,
    # including those raised from Depends() which bypass FastAPI's handler.

    async def dakb_agentic_error(request: Request, exc: AgenticError):
        registry = get_dakb_issue_registry()
        engine = get_dakb_remediation_engine()

        issue_def = registry.get(exc.code)
        if issue_def is None:
            issue = AgenticIssue(
                code=exc.code, severity="blocking", field=exc.field,
                message=exc.message_override or f"Error: {exc.code}",
                retryable=False, category="policy",
                help="/api/v1/help/dakb",
            )
            envelope = AgenticEnvelope.error_response(issues=[issue])
        else:
            issue = issue_def.to_issue(field=exc.field, message_override=exc.message_override)
            remediation = engine.build(issue_def, exc.context)
            envelope = AgenticEnvelope.error_response(issues=[issue], remediation=remediation)

        return JSONResponse(status_code=exc.http_status, content=envelope.to_dict())

    async def dakb_http_error(request: Request, exc: StarletteHTTPException):
        cat_map = {400: "validation", 401: "auth", 403: "policy", 404: "validation",
                   422: "validation", 429: "rate", 500: "dependency", 503: "dependency"}
        category = cat_map.get(exc.status_code, "policy")
        issue = AgenticIssue(
            code=f"HTTP.{exc.status_code}", severity="blocking", field=None,
            message=str(exc.detail) if exc.detail else f"HTTP {exc.status_code}",
            retryable=exc.status_code in (429, 503),
            category=category, help="/api/v1/help/dakb",
        )
        remediation = None
        if exc.status_code == 401:
            remediation = AgenticRemediation(
                goal="Authenticate with a valid DAKB token",
                steps=[RemediationStep(
                    type="action", endpoint="GET /health",
                    prompt="Obtain a valid DAKB JWT token and include it as Authorization: Bearer <token>.",
                )],
            )
        envelope = AgenticEnvelope.error_response(issues=[issue], remediation=remediation)
        headers = dict(exc.headers) if hasattr(exc, "headers") and exc.headers else None
        return JSONResponse(status_code=exc.status_code, content=envelope.to_dict(), headers=headers)

    async def dakb_validation_error(request: Request, exc: RequestValidationError):
        issues = []
        for error in exc.errors():
            field_name = str(error["loc"][-1]) if error.get("loc") else None
            issues.append(AgenticIssue(
                code=f"VALIDATION.INVALID_{(field_name or 'INPUT').upper()}",
                severity="blocking", field=field_name,
                message=error.get("msg", "Validation error"),
                retryable=True, category="validation",
                help=f"/api/v1/help/dakb/errors/VALIDATION.INVALID_{(field_name or 'INPUT').upper()}",
            ))
        remediation = AgenticRemediation(
            goal="Fix invalid input fields and retry",
            steps=[
                RemediationStep(type="generate_field", field=i.field,
                                prompt=f"Provide a valid value for '{i.field}': {i.message}")
                for i in issues if i.field
            ],
            retry_endpoint=str(request.url.path),
        )
        envelope = AgenticEnvelope.error_response(issues=issues, remediation=remediation)
        return JSONResponse(status_code=422, content=envelope.to_dict())

    # Register for BOTH FastAPI and Starlette HTTPException
    # FastAPI handles its own HTTPException separately from Starlette's base class
    app.add_exception_handler(AgenticError, dakb_agentic_error)
    app.add_exception_handler(FastAPIHTTPException, dakb_http_error)
    app.add_exception_handler(StarletteHTTPException, dakb_http_error)
    app.add_exception_handler(RequestValidationError, dakb_validation_error)

    # Middleware to wrap 4xx/5xx JSON responses in agentic envelope
    # This catches auth errors that bypass exception handlers (HTTPBearer, Depends)

    class AgenticErrorMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response = await call_next(request)

            # Only wrap error responses that are still plain JSON (no "issues" key)
            if response.status_code >= 400 and response.headers.get("content-type", "").startswith("application/json"):
                # Read body
                body_bytes = b""
                async for chunk in response.body_iterator:
                    body_bytes += chunk if isinstance(chunk, bytes) else chunk.encode()

                try:
                    body = _json.loads(body_bytes)
                except (ValueError, TypeError):
                    return Response(content=body_bytes, status_code=response.status_code,
                                    headers=dict(response.headers))

                # Skip if already an agentic envelope
                if "issues" in body and "meta" in body:
                    return Response(content=body_bytes, status_code=response.status_code,
                                    headers=dict(response.headers), media_type="application/json")

                # Wrap in agentic envelope
                detail = body.get("detail", str(body))
                cat_map = {400: "validation", 401: "auth", 403: "policy", 404: "validation",
                           422: "validation", 429: "rate", 500: "dependency", 503: "dependency"}
                category = cat_map.get(response.status_code, "policy")
                issue = AgenticIssue(
                    code=f"HTTP.{response.status_code}", severity="blocking", field=None,
                    message=str(detail), retryable=response.status_code in (429, 503),
                    category=category, help="/api/v1/help/dakb",
                )
                remediation = None
                if response.status_code == 401:
                    remediation = AgenticRemediation(
                        goal="Authenticate with a valid DAKB token",
                        steps=[RemediationStep(
                            type="action", endpoint="GET /health",
                            prompt="Include Authorization: Bearer <token> header with a valid DAKB JWT.",
                        )],
                    )
                envelope = AgenticEnvelope.error_response(issues=[issue], remediation=remediation)
                wrapped = _json.dumps(envelope.to_dict())
                headers = dict(response.headers)
                headers["content-length"] = str(len(wrapped))
                return Response(content=wrapped, status_code=response.status_code,
                                headers=headers, media_type="application/json")

            return response

    app.add_middleware(AgenticErrorMiddleware)

    # Add help router
    from .help_router import router as dakb_help_router
    app.include_router(dakb_help_router)

    logger.info(
        f"DAKB agentic handlers registered: "
        f"{len(get_dakb_issue_registry().all_codes())} issues, "
        f"{len(get_dakb_action_registry().all_names())} actions"
    )
