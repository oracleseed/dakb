#!/usr/bin/env python3
"""Bridge Client Runner — long-running process that connects WS to Gateway.

Spawned by client_launcher.py as a detached subprocess.
Connects to the DAKB Gateway via WebSocket, receives messages, buffers to Redis.
Self-terminates if no heartbeat renewal (zombie prevention).

Usage: python3 -m bridge_client_sdk.client_runner <session_id> [--gateway-url URL] [--redis-url URL]

Configuration (env vars, no hard-coded paths):
  DAKB_PROJECT_ROOT — directory containing the importable ``dakb`` package.
                      If unset, it is auto-detected by walking up from this
                      file until a ``dakb`` package is found; otherwise falls
                      back to the current working directory.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path


def _resolve_project_root() -> Path:
    """Locate the directory that makes ``dakb`` importable.

    Precedence:
      1. DAKB_PROJECT_ROOT env var.
      2. Walk up from this file looking for a sibling ``dakb`` package
         (this SDK lives at dakb/bridge/Client_SDK/bridge_client_sdk/).
      3. Current working directory.
    """
    env_root = os.getenv("DAKB_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "dakb" / "__init__.py").exists():
            return parent
    return Path.cwd()


def _load_session_bridge_client():
    """Import SessionBridgeClient, making the ``dakb`` package importable first."""
    root = _resolve_project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from dakb.bridge.session_bridge import SessionBridgeClient
    return SessionBridgeClient


def main():
    parser = argparse.ArgumentParser(description="Bridge Client Runner")
    parser.add_argument("session_id", help="Session ID")
    parser.add_argument("--gateway-url", default="http://localhost:3100")
    parser.add_argument("--redis-url", default="redis://localhost:6379")
    args = parser.parse_args()

    SessionBridgeClient = _load_session_bridge_client()

    client = SessionBridgeClient(
        session_id=args.session_id,
        gateway_url=args.gateway_url,
        redis_url=args.redis_url,
        pid_dir="/tmp",
        fallback_dir="/tmp",
        parent_pid=None,  # No parent monitoring — cleanup hook handles termination
    )

    print(f"Bridge runner starting: session={args.session_id} pid={os.getpid()}")
    sys.stdout.flush()

    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("Bridge runner interrupted")
    except Exception as e:
        print(f"Bridge runner error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
