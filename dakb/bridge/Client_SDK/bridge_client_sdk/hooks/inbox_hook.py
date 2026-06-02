#!/usr/bin/env python3
"""Check Redis inbox for pending bridge messages. Inject into agent context.

IMPORTANT: Only CONSUMES messages on events that support additionalContext
(UserPromptSubmit, PreToolUse, PostToolUse, SessionStart, etc.).
On non-context events (Stop, SubagentStop), messages are left in the inbox.
"""
import json
import os
import sys
from pathlib import Path

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Events that support additionalContext injection
CONTEXT_EVENTS = {
    "UserPromptSubmit", "PreToolUse", "PostToolUse", "SessionStart",
    "PostToolUseFailure", "SubagentStart", "Notification"
}


def _consume_from_redis(session_id):
    """Read and DELETE messages from Redis inbox (destructive read)."""
    import redis
    r = redis.Redis.from_url(REDIS_URL)
    inbox_key = f"bridge:inbox:{session_id}"

    # Check inbox directly (heartbeat may be absent after session resume)
    if not r.exists(inbox_key):
        return None

    raw_messages = r.lrange(inbox_key, 0, -1)
    if not raw_messages:
        return None

    messages = [json.loads(m) for m in raw_messages]
    overflow = 0
    if len(messages) > 20:
        overflow = len(messages) - 20
        archive_key = f"bridge:archive:{session_id}"
        for m in raw_messages[:-20]:
            r.rpush(archive_key, m)
        r.ltrim(archive_key, -100, -1)
        messages = messages[-20:]

    # Destructive: clear inbox after read
    r.delete(inbox_key)

    last_id = messages[-1].get("msg_id", "")
    if last_id:
        r.set(f"bridge:last_seen:{session_id}", last_id)

    return messages, overflow


def _peek_redis(session_id):
    """NON-destructive: check if messages exist without consuming them."""
    import redis
    r = redis.Redis.from_url(REDIS_URL)
    return r.llen(f"bridge:inbox:{session_id}")


def _consume_from_file(session_id):
    """Degraded path: read from fallback file when Redis is down."""
    fallback = Path(f"/tmp/bridge_inbox_{session_id}.jsonl")
    if not fallback.exists():
        return None
    lines = [l for l in fallback.read_text().splitlines() if l.strip()]
    if not lines:
        return None
    messages = [json.loads(l) for l in lines]
    overflow = max(0, len(messages) - 20)
    if overflow:
        messages = messages[-20:]
    fallback.unlink()
    return messages, overflow


def main():
    try:
        raw = sys.stdin.read()
        # Debug: log what the agent CLI sends
        Path("/tmp/bridge_hook_debug.json").write_text(raw)

        hook_input = json.loads(raw) if raw.strip() else {}
        session_id = hook_input.get("session_id", "")

        # Fallback: read session_id from discovery file
        if not session_id:
            discovery = Path("/tmp/bridge_current_session.txt")
            if discovery.exists():
                session_id = discovery.read_text().strip()

        if not session_id:
            sys.exit(0)

        # Always write session_id for discoverability
        Path("/tmp/bridge_current_session.txt").write_text(session_id)

        # Determine hook event
        hook_event = hook_input.get("hook_event_name", "UserPromptSubmit")

        # CRITICAL: Only consume messages on events that support context injection.
        # On other events, leave messages in inbox for the next UserPromptSubmit.
        if hook_event not in CONTEXT_EVENTS:
            sys.exit(0)

        # --- Hybrid bridge: interactive signal + lock check ---
        try:
            import redis
            r = redis.Redis.from_url(REDIS_URL)

            # Signal that an interactive session is active (TTL 30s)
            r.setex(f"bridge:interactive:{session_id}", 30, "1")

            # If watcher daemon holds the lock, skip consume — daemon is responding
            if r.exists(f"bridge:watcher:lock:{session_id}"):
                sys.exit(0)
        except Exception:
            pass  # If Redis check fails, proceed with normal consume

        # Consume messages (destructive read)
        try:
            result = _consume_from_redis(session_id)
        except Exception as e:
            Path("/tmp/bridge_hook_error.log").write_text(f"Redis error: {e}\n")
            result = _consume_from_file(session_id)

        if not result:
            sys.exit(0)

        messages, overflow = result

        lines = []
        if overflow > 0:
            lines.append(f"[{overflow} older messages archived — use /chat-bridge history to view]")
        for msg in messages:
            platform = msg.get("from_platform", "unknown")
            name = msg.get("from_user_name", "Unknown")
            content = msg.get("content", "")
            lines.append(f"[{platform}] {name}: {content}")

        context = "EXTERNAL CHAT MESSAGES (respond via /bridge/send or send_chat_message tool):\n" + "\n".join(lines)

        # Output in the agent CLI's expected format for context injection
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": hook_event,
                "additionalContext": context
            }
        }))
        sys.exit(0)

    except Exception as e:
        # Log any unexpected errors for debugging
        Path("/tmp/bridge_hook_error.log").write_text(f"Unexpected: {e}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
