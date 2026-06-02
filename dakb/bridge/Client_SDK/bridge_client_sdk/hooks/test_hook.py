#!/usr/bin/env python3
"""Minimal test hook to verify additionalContext injection works for UserPromptSubmit."""
import json
import sys


def main():
    raw = sys.stdin.read()
    hook_input = json.loads(raw) if raw.strip() else {}
    hook_event = hook_input.get("hook_event_name", "UserPromptSubmit")

    output = {
        "hookSpecificOutput": {
            "hookEventName": hook_event,
            "additionalContext": "BRIDGE TEST: If you can see this message, additionalContext injection works for " + hook_event
        }
    }

    print(json.dumps(output))
    sys.exit(0)

if __name__ == "__main__":
    main()
