#!/usr/bin/env python3
"""Clean up bridge resources on SessionEnd."""
import json
import os
import signal
import sys
from pathlib import Path

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

def main():
    hook_input = json.loads(sys.stdin.read())
    session_id = hook_input.get("session_id", "")
    if not session_id:
        sys.exit(0)

    # 1. Send "Agent going offline" (best effort)
    try:
        from urllib.request import Request, urlopen
        gateway_url = os.getenv("DAKB_GATEWAY_URL", "http://localhost:3100")
        payload = json.dumps({"session_id": session_id, "text": "Agent going offline"}).encode()
        req = Request(f"{gateway_url}/api/v1/bridge/send",
                     data=payload, headers={"Content-Type": "application/json"})
        urlopen(req, timeout=3)
    except Exception:
        pass

    # 2. Clean up Redis keys (best effort)
    # NOTE: Do NOT delete inbox — messages should persist for session resume.
    # Only clean heartbeat (signals "offline"), last_seen, interactive signal, and watcher keys.
    try:
        import redis
        r = redis.Redis.from_url(REDIS_URL)
        r.delete(f"bridge:heartbeat:{session_id}")
        r.delete(f"bridge:last_seen:{session_id}")
        r.delete(f"bridge:interactive:{session_id}")
        r.delete(f"bridge:watcher:lock:{session_id}")
        r.delete(f"bridge:watcher:notify:{session_id}")
    except Exception:
        pass

    # 3. Kill bridge client process via PID file
    pid_file = Path(f"/tmp/bridge_{session_id}.pid")
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, OSError):
            pass
        try:
            pid_file.unlink()
        except Exception:
            pass

    # 4. Kill watcher daemon process via PID file
    watcher_pid_file = Path(f"/tmp/bridge_watcher_{session_id}.pid")
    if watcher_pid_file.exists():
        try:
            pid = int(watcher_pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, OSError):
            pass
        try:
            watcher_pid_file.unlink()
        except Exception:
            pass

    # 5. Clean up fallback file
    fallback = Path(f"/tmp/bridge_inbox_{session_id}.jsonl")
    if fallback.exists():
        try:
            fallback.unlink()
        except Exception:
            pass

if __name__ == "__main__":
    main()
