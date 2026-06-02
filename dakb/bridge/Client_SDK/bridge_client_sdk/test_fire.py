#!/usr/bin/env python3
"""Push a test message into the current session's bridge inbox.
Run after session restart to verify hooks are live.

Usage: bridge-test-fire [message]
"""
import datetime
import json
import sys
import time
from pathlib import Path

import redis

SESSION_FILE = Path("/tmp/bridge_current_session.txt")

def main():
    if not SESSION_FILE.exists():
        print("No session discovered yet. Send one message first so the hook writes session_id.")
        sys.exit(1)

    session_id = SESSION_FILE.read_text().strip()
    content = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Bridge hook test fire - can you see this?"

    r = redis.Redis(decode_responses=True)
    # Set heartbeat so hook doesn't fast-exit
    r.setex(f"bridge:heartbeat:{session_id}", 60, "alive")

    msg = {
        "msg_id": f"bridge_msg_testfire_{int(time.time())}",
        "session_id": session_id,
        "from_platform": "test",
        "from_user_id": "admin",
        "from_user_name": "TestFire",
        "composite_chat_id": "test:local",
        "content": content,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "msg_type": "user_message"
    }
    inbox_key = f"bridge:inbox:{session_id}"
    r.rpush(inbox_key, json.dumps(msg))

    # Notify watcher daemon
    r.rpush(f"bridge:watcher:notify:{session_id}", "1")

    print(f"Pushed to bridge:inbox:{session_id}")
    print(f"Message: {content}")
    print(f"Inbox depth: {r.llen(inbox_key)}")
    print("Next hook event (UserPromptSubmit, PostToolUse) will inject it into context.")
    print("Watcher daemon (if running) will also pick it up if no interactive session.")

if __name__ == "__main__":
    main()
