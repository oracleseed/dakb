#!/usr/bin/env python3
"""Agent-to-Agent Relay — connects two agent sessions via Redis bridge.

Each session sends messages via POST /api/v1/bridge/agent-send.
The relay watches both sessions' outbound queues and pushes messages
to the other session's inbox.

Usage:
    bridge-relay --session-a SESSION_A --session-b SESSION_B
    bridge-relay --auto   # auto-discover from /tmp/bridge_agent_sessions.json

Architecture:
    Session A (interactive)         Session B (watcher daemon)
         │                                │
         ├─ sends message ───────────────>│ (via Redis relay queue)
         │                                ├─ watcher picks up
         │                                ├─ agent CLI responds
         │<─ response ───────────────────├ (via Redis relay queue)
         ├─ hook injects to context       │
         │                                │

Redis Keys:
    bridge:relay:{session_a}:{session_b}  — outbound from A to B
    bridge:relay:{session_b}:{session_a}  — outbound from B to A
    bridge:relay:sessions                 — JSON with session pair info
"""
import argparse
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("bridge-relay")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
GATEWAY_URL = os.getenv("DAKB_GATEWAY_URL", "http://localhost:3100")
SESSIONS_FILE = Path("/tmp/bridge_agent_sessions.json")
PID_FILE = Path("/tmp/bridge_relay.pid")

# Timing
POLL_INTERVAL = 0.5   # seconds between relay checks
BLPOP_TIMEOUT = 5     # seconds for BLPOP


class AgentRelay:
    """Relay messages between two agent sessions via Redis."""

    def __init__(self, session_a: str, session_b: str):
        self.session_a = session_a
        self.session_b = session_b
        self._running = False
        self._redis = None

    def _relay_key(self, from_session: str, to_session: str) -> str:
        """Redis key for relay queue from one session to another."""
        return f"bridge:relay:{from_session}:{to_session}"

    def _connect(self):
        import redis
        self._redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self._redis.ping()

    def _push_to_inbox(self, target_session: str, msg: dict):
        """Push a message to a session's bridge inbox + notify watcher."""
        inbox_key = f"bridge:inbox:{target_session}"
        notify_key = f"bridge:watcher:notify:{target_session}"
        self._redis.rpush(inbox_key, json.dumps(msg, default=str))
        self._redis.rpush(notify_key, "1")

    def _check_and_relay(self, from_session: str, to_session: str) -> int:
        """Check relay queue and push messages to target inbox. Returns count."""
        relay_key = self._relay_key(from_session, to_session)
        count = 0
        while True:
            raw = self._redis.lpop(relay_key)
            if not raw:
                break
            try:
                msg = json.loads(raw)
                self._push_to_inbox(to_session, msg)
                count += 1
                logger.info(
                    f"Relayed: {from_session[:8]}→{to_session[:8]}: "
                    f"{msg.get('content', '')[:60]}"
                )
            except Exception as e:
                logger.error(f"Relay error: {e}")
        return count

    def run(self):
        """Main relay loop — poll both directions."""
        self._connect()
        self._running = True

        # Write PID
        PID_FILE.write_text(str(os.getpid()))

        # Write session info for discovery
        SESSIONS_FILE.write_text(json.dumps({
            "session_a": self.session_a,
            "session_b": self.session_b,
            "relay_pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2))

        def _handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, shutting down relay")
            self._running = False

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        logger.info("Agent relay started:")
        logger.info(f"  Session A: {self.session_a}")
        logger.info(f"  Session B: {self.session_b}")
        logger.info(f"  PID: {os.getpid()}")

        try:
            while self._running:
                # Relay A→B
                self._check_and_relay(self.session_a, self.session_b)
                # Relay B→A
                self._check_and_relay(self.session_b, self.session_a)
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            pass
        finally:
            PID_FILE.unlink(missing_ok=True)
            logger.info("Agent relay stopped")


def send_to_agent(from_session: str, to_session: str, text: str,
                  from_name: str = "Agent"):
    """Send a message from one agent session to another via the relay queue.

    Can be called directly from Python or via the CLI.
    """
    import redis
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

    msg = {
        "msg_id": f"bridge_msg_relay_{int(time.time() * 1000)}",
        "session_id": to_session,
        "from_platform": "agent",
        "from_user_id": from_session[:8],
        "from_user_name": from_name,
        "composite_chat_id": f"agent:{from_session[:8]}",
        "content": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "msg_type": "agent_message",
        "source_session": from_session,
    }

    relay_key = f"bridge:relay:{from_session}:{to_session}"
    r.rpush(relay_key, json.dumps(msg, default=str))

    # Also push directly to inbox + notify (relay may not be running)
    inbox_key = f"bridge:inbox:{to_session}"
    notify_key = f"bridge:watcher:notify:{to_session}"
    r.rpush(inbox_key, json.dumps(msg, default=str))
    r.rpush(notify_key, "1")

    return msg["msg_id"]


def main():
    parser = argparse.ArgumentParser(
        description="Agent-to-Agent Relay — connect two agent sessions"
    )
    sub = parser.add_subparsers(dest="command")

    # relay command
    relay_cmd = sub.add_parser("relay", help="Start the relay daemon")
    relay_cmd.add_argument("--session-a", required=True, help="Session A ID")
    relay_cmd.add_argument("--session-b", required=True, help="Session B ID")

    # send command
    send_cmd = sub.add_parser("send", help="Send a message to another agent")
    send_cmd.add_argument("--from", dest="from_session", required=True)
    send_cmd.add_argument("--to", dest="to_session", required=True)
    send_cmd.add_argument("--name", default="Agent", help="Sender display name")
    send_cmd.add_argument("message", nargs="+", help="Message text")

    # status command
    sub.add_parser("status", help="Show relay status")

    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "relay":
        relay = AgentRelay(args.session_a, args.session_b)
        relay.run()
    elif args.command == "send":
        msg_id = send_to_agent(
            args.from_session, args.to_session,
            " ".join(args.message), args.name
        )
        print(f"Sent: {msg_id}")
    elif args.command == "status":
        if SESSIONS_FILE.exists():
            info = json.loads(SESSIONS_FILE.read_text())
            print(json.dumps(info, indent=2))
            if PID_FILE.exists():
                pid = int(PID_FILE.read_text().strip())
                try:
                    os.kill(pid, 0)
                    print(f"Relay: RUNNING (pid={pid})")
                except OSError:
                    print(f"Relay: STALE (pid={pid})")
            else:
                print("Relay: NOT RUNNING")
        else:
            print("No relay sessions configured.")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
