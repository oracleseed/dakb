"""DAKB Agentic Help Router — auto-generated self-documentation.

Endpoints:
  GET /api/v1/help/dakb              — Full help index
  GET /api/v1/help/dakb/errors       — All error codes
  GET /api/v1/help/dakb/errors/{code}— Error deep-dive
  GET /api/v1/help/dakb/actions      — All actions with safety labels
  GET /api/v1/help/dakb/actions/{name} — Action deep-dive
  GET /api/v1/help/dakb/envelope     — Response envelope spec
"""

from fastapi import APIRouter

from . import get_dakb_action_registry, get_dakb_issue_registry

router = APIRouter(prefix="/api/v1/help/dakb", tags=["DAKB Help"])


@router.get("")
async def help_index():
    issues = get_dakb_issue_registry()
    actions = get_dakb_action_registry()
    return {
        "title": "DAKB Gateway — Agentic API Help Center",
        "schema_version": "1.0",
        "sections": {
            "errors": {"count": len(issues.all_codes()), "endpoint": "/api/v1/help/dakb/errors", "codes": issues.all_codes()},
            "actions": {"count": len(actions.all_names()), "endpoint": "/api/v1/help/dakb/actions", "names": actions.all_names()},
            "envelope": {"endpoint": "/api/v1/help/dakb/envelope"},
        },
    }


@router.get("/errors")
async def help_list_errors():
    issues = get_dakb_issue_registry()
    return {"errors": [
        {"code": d.code, "severity": d.severity, "category": d.category,
         "retryable": d.retryable, "message": d.message,
         "help": f"/api/v1/help/dakb/errors/{d.code}"}
        for d in issues.all_definitions()
    ]}


@router.get("/errors/{code:path}")
async def help_error_detail(code: str):
    issues = get_dakb_issue_registry()
    d = issues.get(code)
    if not d:
        return {"error": "not_found", "available_codes": issues.all_codes()}
    return {
        "code": d.code, "severity": d.severity, "category": d.category,
        "retryable": d.retryable, "message": d.message,
        "remediation_strategy": d.remediation_key, "deprecated": d.deprecated,
        "related_errors": [x.code for x in issues.by_category(d.category) if x.code != d.code],
    }


@router.get("/actions")
async def help_list_actions():
    actions = get_dakb_action_registry()
    return {"actions": [
        {"name": d.name, "method": d.method, "endpoint": d.endpoint,
         "safe": d.safe, "idempotent": d.idempotent, "risk": d.risk,
         "description": d.description, "help": f"/api/v1/help/dakb/actions/{d.name}"}
        for d in actions.all_definitions()
    ]}


@router.get("/actions/{name}")
async def help_action_detail(name: str):
    actions = get_dakb_action_registry()
    d = actions.get(name)
    if not d:
        return {"error": "not_found", "available_actions": actions.all_names()}
    return {
        "name": d.name, "method": d.method, "endpoint": d.endpoint,
        "safe": d.safe, "idempotent": d.idempotent, "risk": d.risk,
        "requires_confirmation": d.requires_confirmation,
        "description": d.description, "parameters_schema": d.parameters_schema,
    }


@router.get("/envelope")
async def help_envelope_spec():
    return {
        "title": "DAKB Gateway — Agentic Response Envelope",
        "schema_version": "1.0",
        "fields": {
            "status": {"type": "string", "values": ["ok", "created", "deleted", "content_guidance", "error", "rate_limited", "unauthorized"]},
            "data": {"type": "any", "description": "Primary response payload"},
            "issues": {"type": "array[Issue]", "description": "Typed problems with codes, severity, remediation links"},
            "remediation": {"type": "Remediation", "description": "Ordered action plan for self-correction"},
            "available_actions": {"type": "array[Action]", "description": "Contextual next steps with safety labels"},
            "constraints": {"type": "object", "description": "Hard limits (rate, size, scopes)"},
            "suggestions": {"type": "array[string]", "description": "Proactive hints"},
            "meta": {"type": "object", "description": "trace_id, timestamp, schema_version"},
            "help": {"type": "string", "description": "Link to this help center"},
        },
    }
