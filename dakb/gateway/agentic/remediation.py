"""DAKB-specific remediation strategies.

Extends the base RemediationEngine with strategies for DAKB knowledge base
operations: knowledge CRUD, search, moderation, sessions, messaging, vault.
"""

from dakb.agentic_core.envelope import AgenticRemediation, RemediationStep


def kb_not_found(issue_def, context):
    kid = context.get("knowledge_id", "unknown")
    return AgenticRemediation(
        goal=f"Find the correct knowledge entry (requested: {kid})",
        steps=[
            RemediationStep(type="action", endpoint="GET /api/v1/knowledge/search",
                            prompt="Search for the knowledge entry by keywords or tags."),
            RemediationStep(type="action", endpoint="GET /api/v1/knowledge/stats",
                            prompt="Check KB statistics to verify entries exist."),
        ],
        retry_endpoint=context.get("retry_endpoint"),
    )


def kb_validation(issue_def, context):
    missing = context.get("missing_fields", [])
    return AgenticRemediation(
        goal=f"Fix validation errors: {', '.join(missing) if missing else 'see issues'}",
        steps=[
            RemediationStep(type="generate_field", field=f, prompt=f"Provide a valid value for '{f}'")
            for f in missing
        ] or [RemediationStep(type="generate_field", prompt="Fix the invalid input fields")],
        retry_endpoint=context.get("retry_endpoint"),
    )


def kb_duplicate(issue_def, context):
    existing_id = context.get("existing_id", "")
    return AgenticRemediation(
        goal="Avoid duplicating existing knowledge",
        steps=[
            RemediationStep(type="action", endpoint=f"GET /api/v1/knowledge/{existing_id}",
                            prompt=f"Read the existing entry {existing_id} and post a thread comment instead of creating a duplicate."),
            RemediationStep(type="alternatives", options=[
                f"Post a suggestion thread on {existing_id} with new information",
                "Vote on the existing entry if it's already complete",
                "Store as a new entry only if the angle is genuinely different",
            ]),
        ],
    )


def auth_required(issue_def, context):
    return AgenticRemediation(
        goal="Authenticate with a valid DAKB token",
        steps=[
            RemediationStep(type="action", endpoint="GET /health",
                            prompt="Include Authorization: Bearer <token> header. Token is a DAKB JWT."),
        ],
    )


def rate_limited(issue_def, context):
    retry_after = context.get("retry_after", 60)
    return AgenticRemediation(
        goal=f"Wait {retry_after}s for rate limit reset",
        steps=[RemediationStep(type="wait", seconds=retry_after)],
        retry_endpoint=context.get("retry_endpoint"),
    )


def session_not_found(issue_def, context):
    return AgenticRemediation(
        goal="Start a new session or find the correct session ID",
        steps=[
            RemediationStep(type="action", endpoint="POST /api/v1/sessions",
                            prompt="Start a new session with project_path and task_description."),
        ],
    )


def moderation_denied(issue_def, context):
    action = context.get("action", "moderate")
    return AgenticRemediation(
        goal=f"Get permission for {action} action",
        steps=[
            RemediationStep(type="alternatives", options=[
                "approve: requires admin role",
                "delete: requires admin role OR be the entry creator (with user_confirmed=true)",
                "deprecate: any authenticated agent (with user_confirmed=true)",
            ]),
        ],
    )


def vault_file_error(issue_def, context):
    return AgenticRemediation(
        goal="Fix file upload issues",
        steps=[
            RemediationStep(
                type="action",
                endpoint="GET /api/v1/vault/preflight?mime={your_mime_type}&size={file_size}",
                prompt=(
                    "Call preflight to get the live allow-list and budget. "
                    "It returns allowed_mime_types[], max_file_size_bytes, and "
                    "max_files_per_entry. Never hardcode the allowed list — "
                    "preflight is the source of truth."
                ),
            ),
            RemediationStep(type="alternatives", options=[
                "Vault accepts ~all common non-executable formats: documents (pdf, docx, xlsx, pptx, odt), text/code (md, txt, html, json, yaml, py, js, sql), images (png, jpeg, gif, webp, svg, tiff, heic), audio/video (mp3, wav, mp4, webm), archives (zip, tar, gz, bz2, xz, rar, 7z, zstd), fonts (ttf, woff2), and ML data (parquet, hdf5, numpy, octet-stream)",
                "Executable binaries (ELF, Mach-O, PE, Java class, shebang scripts) are ALWAYS rejected by the executable detector regardless of declared MIME — convert to a data format or remove",
                "If you got VAULT.EXECUTABLE_REJECTED: the file content matched executable magic bytes. Do not retry with a different MIME — the detector inspects bytes, not headers",
                "Maximum 10 files per knowledge entry, 500MB total per entry",
            ]),
        ],
    )


def message_not_found(issue_def, context):
    return AgenticRemediation(
        goal="Find the correct message",
        steps=[
            RemediationStep(type="action", endpoint="GET /api/v1/messages",
                            prompt="List your messages to find the correct message ID."),
        ],
    )


def alias_conflict(issue_def, context):
    alias = context.get("alias", "")
    return AgenticRemediation(
        goal=f"Resolve alias conflict for '{alias}'",
        steps=[
            RemediationStep(type="action", endpoint="GET /api/v1/aliases",
                            prompt="List existing aliases to find available names."),
            RemediationStep(type="alternatives", options=[
                "Choose a different alias name",
                "Deactivate the existing alias first (if yours)",
            ]),
        ],
    )


def whiteboard_version_conflict(issue_def, context):
    current = context.get("current_version", "?")
    return AgenticRemediation(
        goal=f"Resolve whiteboard version conflict (server has version {current})",
        steps=[
            RemediationStep(type="action", endpoint="GET /api/v1/whiteboard/boards/{board_id}",
                            prompt=f"Read the current board state to get version {current}, then retry with the correct version."),
        ],
        retry_endpoint=context.get("retry_endpoint"),
    )


def mcp_type_mismatch(issue_def, context):
    """Remediation for MCP tool argument type mismatches.

    Common trigger: an agent builds tool_call arguments by pasting JSON into
    string literals (e.g. tags='["a","b"]' or version="18"). The MCP HTTP
    transport strict-checks JSON types via isinstance(), so quoted strings
    do NOT coerce to arrays/ints. The fix is always on the caller side:
    pass the real JSON type, not a stringified copy of it.
    """
    field = context.get("field", "<unknown>")
    expected = context.get("expected_type", "<declared type>")
    received = context.get("received_type", "<unknown>")
    return AgenticRemediation(
        goal=(
            f"Re-send the MCP tool call with field '{field}' as a real "
            f"{expected} value (received: {received})"
        ),
        steps=[
            RemediationStep(
                type="action",
                endpoint="MCP tools/call",
                prompt=(
                    f"Resend the same tool_call but pass '{field}' as a JSON "
                    f"{expected}, NOT as a quoted string. The MCP HTTP "
                    "transport preserves JSON types on the wire — if you "
                    "send a string, the server validator (isinstance check) "
                    "rejects it with MCP.TYPE_MISMATCH."
                ),
            ),
            RemediationStep(type="alternatives", options=[
                'array:   tags=["a","b"]   NOT   tags="[\\"a\\",\\"b\\"]"',
                'integer: version=18        NOT   version="18"',
                'number:  confidence=0.8    NOT   confidence="0.8"',
                'boolean: dry_run=true      NOT   dry_run="true"',
                "Root cause is almost always on the caller side: the agent "
                "built tool arguments by templating strings rather than "
                "producing native JSON values. There is no MCP serialization "
                "bug to work around — do not fall back to the REST API.",
                "Verify: call GET /api/v1/help/dakb/errors/MCP.TYPE_MISMATCH "
                "for full diagnostic details and retry guidance.",
            ]),
        ],
        retry_endpoint=context.get("retry_endpoint"),
    )


# Strategy registry — keys match remediation_key in issues.yaml
DAKB_STRATEGIES = {
    "kb_not_found": kb_not_found,
    "kb_validation": kb_validation,
    "kb_duplicate": kb_duplicate,
    "auth_required": auth_required,
    "rate_limited": rate_limited,
    "session_not_found": session_not_found,
    "moderation_denied": moderation_denied,
    "vault_file_error": vault_file_error,
    "message_not_found": message_not_found,
    "alias_conflict": alias_conflict,
    "whiteboard_version_conflict": whiteboard_version_conflict,
    "mcp_type_mismatch": mcp_type_mismatch,
}
