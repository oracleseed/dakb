#!/usr/bin/env python3
"""Send automatic event notifications to linked external chats."""
import json
import os
import sys
from urllib.request import Request, urlopen

GATEWAY_URL = os.getenv("DAKB_GATEWAY_URL", "http://localhost:3100")

def main():
    hook_input = json.loads(sys.stdin.read())
    session_id = hook_input.get("session_id", "")
    event = hook_input.get("hook_event_name", "")
    if not session_id:
        sys.exit(0)

    # Format event message
    if event == "TaskCompleted":
        text = f"Task completed in session {session_id[:8]}..."
    elif event == "TeammateIdle":
        text = "Agent teammate idle — awaiting input"
    else:
        sys.exit(0)

    # POST to bridge/send (fire-and-forget)
    try:
        payload = json.dumps({"session_id": session_id, "text": text}).encode()
        req = Request(f"{GATEWAY_URL}/api/v1/bridge/send",
                     data=payload, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=3)
    except Exception:
        pass  # Async hook — don't block the agent

if __name__ == "__main__":
    main()
