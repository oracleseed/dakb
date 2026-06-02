"""Agent Launcher -- validates and executes agent launch from external chat.

SECURITY -- DENY BY DEFAULT:
  Launching an agent process from an inbound chat message is a powerful and
  dangerous capability (remote command execution surface). It is therefore
  GATED OFF by default and only ever permitted when ALL of the following hold:

    1. The environment flag ``DAKB_BRIDGE_ALLOW_AGENT_LAUNCH`` is set to a
       truthy value ("1", "true", "yes", "on"). Absent/false -> always denied.
    2. The per-agent ``LaunchConfig.enabled`` flag is True.
    3. The requesting platform user id is present in ``LaunchConfig.allowed_users``
       (an explicit allowlist; empty -> always denied).

  No identities are hard-coded anywhere. Operators must opt in explicitly via
  the environment flag and supply their own allowlist.
"""
import logging
import os
import re

from .models import LaunchConfig

logger = logging.getLogger(__name__)

RATE_LIMIT_KEY = "bridge:launch_rate:{user_id}"

# Global kill-switch: agent-launch-from-chat is OFF unless explicitly enabled.
_TRUTHY = {"1", "true", "yes", "on"}


def agent_launch_globally_enabled() -> bool:
    """Return True only if the operator has explicitly opted in via env flag."""
    return os.getenv("DAKB_BRIDGE_ALLOW_AGENT_LAUNCH", "").strip().lower() in _TRUTHY


class AgentLauncher:
    """Validates and builds agent launch commands (deny-by-default)."""

    def __init__(self, redis):
        self._redis = redis

    async def validate_launch(self, user_id: int, config: LaunchConfig) -> tuple[bool, str]:
        """Validate a launch request. Returns (ok, error_message).

        Denies unless the global env flag is set, the per-agent config is
        enabled, and the user is on the explicit allowlist.
        """
        # Gate 1: global kill-switch (env-driven, default deny)
        if not agent_launch_globally_enabled():
            return False, (
                "Agent launch from chat is disabled. Set "
                "DAKB_BRIDGE_ALLOW_AGENT_LAUNCH=true to enable (deny by default)."
            )

        # Gate 2: per-agent enable flag
        if not config.enabled:
            return False, "Agent launch is disabled for this agent."

        # Gate 3: explicit allowlist (empty -> deny)
        if user_id not in config.allowed_users:
            return False, f"User {user_id} is not authorized to launch this agent."

        # Rate limit check
        rate_key = RATE_LIMIT_KEY.format(user_id=user_id)
        count = await self._redis.incr(rate_key)
        if count == 1:
            await self._redis.expire(rate_key, config.rate_limit_window)
        if count > config.rate_limit_max:
            return False, f"Rate limit exceeded ({config.rate_limit_max} per {config.rate_limit_window}s)."

        return True, ""

    _SESSION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

    def build_command(self, template: str, session_id: str, message: str) -> str:
        """Build a safe launch command from template. Escapes user input.

        Validates session_id is alphanumeric (safe by construction).
        Uses backslash escaping for characters dangerous in double-quoted
        shell contexts: ", \\, $, `, ;, &, |, (, ), <, >, newline, etc.
        """
        if not self._SESSION_ID_PATTERN.match(session_id):
            raise ValueError(f"Invalid session_id format: {session_id}")
        escaped = []
        for c in message:
            if c in ('"', '\\', '$', '`', ';', '&', '|', '(', ')', '<', '>', '\n', "'", '{', '}'):
                escaped.append(f'\\{c}')
            else:
                escaped.append(c)
        return template.format(
            session_id=session_id,
            escaped_message=''.join(escaped),
        )
