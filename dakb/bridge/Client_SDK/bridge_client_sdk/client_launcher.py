#!/usr/bin/env python3
"""Launch SessionBridgeClient as a background process.

Usage:
    bridge-launcher <session_id> [--gateway-url URL] [--chat-id CHAT_ID]

The launcher:
  1. Checks for existing bridge process (PID guard)
  2. Creates a bridge link via Gateway REST API (if chat_id provided)
  3. Spawns a bridge runner subprocess (detached)
  4. Sets initial heartbeat in Redis

Configuration (env vars, no hard-coded paths):
  DAKB_VENV_PYTHON   — Python interpreter used to spawn the runner
                       (defaults to the current interpreter, sys.executable).
  DAKB_GATEWAY_URL   — Gateway base URL (default http://localhost:3100).
  REDIS_URL          — Redis URL (default redis://localhost:6379).
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

# Resolve the Python interpreter for the detached runner. Prefer an explicit
# env override, otherwise reuse the interpreter currently running this script.
VENV_PYTHON = os.getenv("DAKB_VENV_PYTHON", sys.executable)

PID_DIR = Path("/tmp")
GATEWAY_URL = os.getenv("DAKB_GATEWAY_URL", "http://localhost:3100")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
DEFAULT_AGENT_ID = os.getenv("DAKB_BRIDGE_AGENT_ID", "default-agent")


def get_existing_pid(session_id: str) -> int | None:
    """Check if a bridge process is already running for this session."""
    pid_file = PID_DIR / f"bridge_{session_id}.pid"
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # Check if alive
        return pid
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return None


def create_link(session_id: str, chat_id: str, agent_id: str = DEFAULT_AGENT_ID) -> bool:
    """Create a bridge link via Gateway REST API."""
    payload = json.dumps({
        "session_id": session_id,
        "agent_id": agent_id,
        "composite_chat_id": chat_id,
        "platform": chat_id.split(":")[0] if ":" in chat_id else "unknown",
        "linked_by": "bridge-launcher"
    }).encode()
    req = Request(
        f"{GATEWAY_URL}/api/v1/bridge/link",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        resp = urlopen(req, timeout=5)
        result = json.loads(resp.read().decode())
        return result.get("status") == "linked"
    except Exception as e:
        print(f"Warning: Failed to create link: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Launch Session Bridge Client")
    parser.add_argument("session_id", help="Agent session ID")
    parser.add_argument("--gateway-url", default=GATEWAY_URL, help="DAKB Gateway URL")
    parser.add_argument("--redis-url", default=REDIS_URL, help="Redis URL")
    parser.add_argument("--chat-id", help="Composite chat ID to link (e.g. telegram:12345)")
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID, help="Agent ID for the link")
    args = parser.parse_args()

    # 1. Check for existing bridge
    existing = get_existing_pid(args.session_id)
    if existing:
        print(json.dumps({
            "status": "already_running",
            "pid": existing,
            "session_id": args.session_id
        }))
        return

    # 2. Create link if chat_id provided
    if args.chat_id:
        linked = create_link(args.session_id, args.chat_id, args.agent_id)
        if not linked:
            print(json.dumps({
                "status": "link_failed",
                "session_id": args.session_id,
                "chat_id": args.chat_id
            }))
            return

    # 3. Spawn bridge runner as detached subprocess
    runner_module = "bridge_client_sdk.client_runner"
    log_path = PID_DIR / f"bridge_{args.session_id}.log"

    with open(log_path, "w") as log_file:
        proc = subprocess.Popen(
            [VENV_PYTHON, "-m", runner_module,
             args.session_id,
             "--gateway-url", args.gateway_url,
             "--redis-url", args.redis_url],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # Detach from parent
        )

    # 4. Set initial heartbeat via Redis
    try:
        import redis
        r = redis.Redis.from_url(args.redis_url)
        r.setex(f"bridge:heartbeat:{args.session_id}", 60, "alive")
    except Exception:
        pass

    print(json.dumps({
        "status": "started",
        "pid": proc.pid,
        "session_id": args.session_id,
        "gateway_url": args.gateway_url,
        "chat_id": args.chat_id,
        "log": str(log_path)
    }))


if __name__ == "__main__":
    main()
