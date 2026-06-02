"""MCP Elicitation support — user confirmation during tool execution.

Implements MCP Form Mode Elicitation (spec 2025-06-18).
During a tools/call handler, the server can send an elicitation/create
request to the client, which shows a confirmation UI to the user.
The user accepts, declines, or cancels — and the response flows back.

Usage in handlers::

    from .elicitation import elicit_confirmation

    result = await elicit_confirmation(message="Delete entry 'kn_xxx'?")
    if not result.accepted:
        return ToolResponse(success=False, error="Cancelled by user")

IMPORTANT: Do NOT add a required boolean "confirm" field to the schema.
Some MCP client form UIs cannot reliably toggle a required checkbox before
Accept, which produces a dead-loop where Accept keeps re-opening the dialog.
The Accept / Decline buttons ARE the confirmation — per MCP spec the action
field ("accept" | "decline" | "cancel") is the signal. Use an empty schema
(default) or inputs that do not block submit.
"""

import asyncio
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ElicitationResult:
    """Result of an elicitation request."""
    action: str  # "accept", "decline", "cancel"
    content: dict | None = field(default=None)

    @property
    def accepted(self) -> bool:
        return self.action == "accept"

    @property
    def declined(self) -> bool:
        return self.action == "decline"

    @property
    def cancelled(self) -> bool:
        return self.action == "cancel"


async def elicit_confirmation(
    message: str,
    schema: dict[str, Any] | None = None,
) -> ElicitationResult:
    """Send an elicitation/create request and await user response.

    Writes a JSON-RPC request to stdout (the MCP client reads it)
    and reads the response from stdin. This happens within an active
    tools/call handler — the main server loop is awaiting us.

    Args:
        message: Human-readable prompt shown to the user.
        schema: JSON Schema for form fields (primitives only per MCP spec).
                If None, an empty-properties schema is used.

    Returns:
        ElicitationResult with the user's action and form content.
    """
    request_id = f"elicit_{uuid.uuid4().hex[:8]}"

    if schema is None:
        # Empty-properties schema — the Accept / Decline buttons alone signal
        # the user's decision. Do NOT add a required boolean here: some MCP
        # client form UIs cannot reliably toggle required checkboxes before
        # Accept, producing a dead-loop on every confirmation attempt.
        schema = {
            "type": "object",
            "properties": {},
        }

    # Build and send elicitation/create request to client
    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "elicitation/create",
        "params": {
            "message": message,
            "requestedSchema": schema,
        },
    }

    logger.info(f"Sending elicitation request: {request_id}")
    print(json.dumps(request), flush=True)

    # Read response from stdin (blocking until user responds)
    loop = asyncio.get_event_loop()
    try:
        line = await loop.run_in_executor(None, sys.stdin.readline)
    except Exception as e:
        logger.error(f"Error reading elicitation response: {e}")
        return ElicitationResult(action="cancel")

    if not line or not line.strip():
        logger.warning("Empty response to elicitation — treating as cancel")
        return ElicitationResult(action="cancel")

    try:
        response = json.loads(line.strip())
        result = response.get("result", {})
        action = result.get("action", "cancel")
        content = result.get("content")

        logger.info(f"Elicitation response: action={action}")
        return ElicitationResult(action=action, content=content)

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"Failed to parse elicitation response: {e}")
        return ElicitationResult(action="cancel")
