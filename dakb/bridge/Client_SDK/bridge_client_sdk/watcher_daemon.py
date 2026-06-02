#!/usr/bin/env python3
"""Hybrid Bridge Watcher Daemon — auto-responds to external chat when the agent is idle.

Architecture:
  Path A (User active): UserPromptSubmit hook consumes inbox, injects additionalContext.
  Path B (Agent standby): This daemon BLPOPs a notify key, invokes the agent CLI
    (`claude -p --resume`), and routes responses back to external chat via the bridge API.

Dual-response prevention:
  - bridge:interactive:{session_id}  (TTL 30s) — set by hook → daemon defers
  - bridge:watcher:lock:{session_id} (TTL 120s) — set by daemon → hook defers

Usage:
    bridge-watcher --session-id SESSION_ID
    bridge-watcher  # auto-discovers from /tmp/bridge_current_session.txt
"""
import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

logger = logging.getLogger("bridge-watcher")

# --- Constants ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
GATEWAY_URL = os.getenv("DAKB_GATEWAY_URL", "http://localhost:3100")
SESSION_FILE = Path("/tmp/bridge_current_session.txt")
PID_DIR = Path("/tmp")

# Timing
BLPOP_TIMEOUT = 30           # seconds to wait for notify signal
BATCH_DELAY = 2.0            # seconds to wait for additional messages before responding
COOLDOWN = 5.0               # seconds between response cycles
LOCK_TTL = 120               # seconds — max time for the agent CLI to respond
INTERACTIVE_CHECK_TTL = 30   # must match inbox_hook.py

# Limits
MAX_BUDGET_USD = 0.50
MAX_REDIS_FAILURES = 10
MAX_CLAUDE_FAILURES = 5

# Agent CLI command (the interactive coding-agent CLI, e.g. "claude")
CLAUDE_CMD = os.getenv("DAKB_AGENT_CLI", "claude")

# Config file (optional overrides)
CONFIG_FILE = Path(__file__).parent / "config" / "watcher_config.json"


def _project_root() -> Path:
    """Resolve the working directory for agent CLI invocations.

    Order of precedence:
      1. DAKB_PROJECT_ROOT env var (operator-supplied).
      2. The current working directory (no hard-coded home paths).
    """
    env_root = os.getenv("DAKB_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    return Path.cwd()


def _load_config() -> dict:
    """Load optional config overrides from watcher_config.json."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def _discover_session_id() -> str | None:
    """Auto-discover session ID from the discovery file."""
    if SESSION_FILE.exists():
        sid = SESSION_FILE.read_text().strip()
        if sid:
            return sid
    return None


def _find_session_jsonl(session_id: str) -> Path | None:
    """Find the JSONL transcript file for a session to verify it exists."""
    projects_dir = Path.home() / ".claude" / "projects"
    for jsonl in projects_dir.rglob(f"{session_id}.jsonl"):
        return jsonl
    return None


class WatcherDaemon:
    """Main watcher daemon — BLPOP loop + agent CLI invocation."""

    def __init__(self, session_id: str, config: dict | None = None,
                 no_resume: bool = False, reply_to: str | None = None,
                 agent_name: str = "Agent B"):
        self.session_id = session_id
        self.config = config or _load_config()
        self.no_resume = no_resume
        self.reply_to = reply_to  # Session ID to route responses back to
        self.agent_name = agent_name
        self._running = False
        self._redis = None
        self._redis_failures = 0
        self._claude_failures = 0

        # Apply config overrides
        self.blpop_timeout = self.config.get("blpop_timeout", BLPOP_TIMEOUT)
        self.batch_delay = self.config.get("batch_delay", BATCH_DELAY)
        self.cooldown = self.config.get("cooldown", COOLDOWN)
        self.lock_ttl = self.config.get("lock_ttl", LOCK_TTL)
        self.max_budget = self.config.get("max_budget_usd", MAX_BUDGET_USD)
        self.allowed_tools = self.config.get(
            "allowed_tools", "Read,Bash,Grep,Glob,WebFetch,WebSearch"
        )
        self.system_prompt = self.config.get(
            "system_prompt",
            "You are responding to external chat messages forwarded via the Session Bridge. "
            "Keep responses concise and helpful. If you need to take actions, do so. "
            "Format your reply as plain text suitable for a chat platform."
        )

    def _write_pid(self) -> None:
        pid_file = PID_DIR / f"bridge_watcher_{self.session_id}.pid"
        pid_file.write_text(str(os.getpid()))

    def _cleanup_pid(self) -> None:
        pid_file = PID_DIR / f"bridge_watcher_{self.session_id}.pid"
        pid_file.unlink(missing_ok=True)

    def _connect_redis(self):
        """Connect to Redis (synchronous client for BLPOP)."""
        import redis
        self._redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self._redis.ping()
        self._redis_failures = 0

    def _is_interactive_active(self) -> bool:
        """Check if an interactive agent session is actively responding."""
        return bool(self._redis.exists(f"bridge:interactive:{self.session_id}"))

    def _acquire_lock(self) -> bool:
        """Acquire watcher lock (SETNX with TTL). Returns True if acquired."""
        lock_key = f"bridge:watcher:lock:{self.session_id}"
        acquired = self._redis.set(lock_key, str(os.getpid()), nx=True, ex=self.lock_ttl)
        return bool(acquired)

    def _release_lock(self) -> None:
        """Release watcher lock."""
        self._redis.delete(f"bridge:watcher:lock:{self.session_id}")

    def _consume_inbox(self) -> list[dict]:
        """Consume all pending messages from Redis inbox (atomic pipeline)."""
        inbox_key = f"bridge:inbox:{self.session_id}"
        pipe = self._redis.pipeline()
        pipe.lrange(inbox_key, 0, -1)
        pipe.delete(inbox_key)
        results = pipe.execute()
        raw_messages = results[0]
        if not raw_messages:
            return []
        messages = []
        for raw in raw_messages:
            try:
                messages.append(json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode()))
            except (json.JSONDecodeError, AttributeError):
                continue
        return messages

    def _drain_notify_queue(self) -> None:
        """Clear all pending notify signals (we've already consumed the inbox)."""
        self._redis.delete(f"bridge:watcher:notify:{self.session_id}")

    def _format_prompt(self, messages: list[dict]) -> str:
        """Format messages into a prompt for the agent CLI."""
        lines = []
        for msg in messages:
            platform = msg.get("from_platform", "unknown")
            name = msg.get("from_user_name", "Unknown")
            content = msg.get("content", "")
            lines.append(f"[{platform}] {name}: {content}")

        return (
            "External chat messages received via Session Bridge:\n"
            + "\n".join(lines)
            + "\n\nPlease respond to these messages."
        )

    def _invoke_claude(self, prompt: str) -> str | None:
        """Invoke the agent CLI (`claude -p`) and capture response.

        In resume mode (default): uses --resume SESSION_ID to maintain context.
        In no-resume mode: fresh invocation each time (for standalone agent sessions).
        """
        cmd = [
            "env", "-u", "CLAUDECODE",
            CLAUDE_CMD,
            "-p", prompt,
        ]
        if not self.no_resume:
            cmd.extend(["--resume", self.session_id])
        cmd.extend([
            "--output-format", "json",
            "--permission-mode", "dontAsk",
            "--max-budget-usd", str(self.max_budget),
            "--allowedTools", self.allowed_tools,
            "--append-system-prompt", self.system_prompt,
        ])

        logger.info(f"Invoking agent CLI (session={self.session_id[:8]}...)")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute hard timeout
                cwd=str(_project_root()),
            )
            if result.returncode != 0:
                logger.error(f"agent CLI failed (rc={result.returncode}): {result.stderr[:200]}")
                self._claude_failures += 1
                return None

            # Parse JSON output
            try:
                output = json.loads(result.stdout)
                response = output.get("result", "")
                if not response:
                    response = output.get("content", result.stdout.strip())
                self._claude_failures = 0
                return response
            except json.JSONDecodeError:
                # Plain text output
                self._claude_failures = 0
                return result.stdout.strip()

        except subprocess.TimeoutExpired:
            logger.error("agent CLI timed out (300s)")
            self._claude_failures += 1
            return None
        except FileNotFoundError:
            logger.error(f"'{CLAUDE_CMD}' not found in PATH")
            self._claude_failures += 1
            return None

    def _send_response(self, text: str) -> bool:
        """Route response back — either to external chat or to another agent's inbox."""
        # Agent-to-agent mode: push response directly to reply_to session's inbox
        if self.reply_to:
            return self._send_to_agent_inbox(text)

        # External chat mode: POST to bridge API
        payload = json.dumps({
            "session_id": self.session_id,
            "text": text,
        }).encode()
        req = Request(
            f"{GATEWAY_URL}/api/v1/bridge/send",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            resp = urlopen(req, timeout=10)
            result = json.loads(resp.read().decode())
            logger.info(f"Response sent: {result.get('delivered_to', 0)} chats")
            return True
        except Exception as e:
            logger.error(f"Failed to send response: {e}")
            return False

    def _send_to_agent_inbox(self, text: str) -> bool:
        """Push response to another agent session's Redis inbox."""
        try:
            msg = {
                "msg_id": f"bridge_msg_agent_{int(time.time() * 1000)}",
                "session_id": self.reply_to,
                "from_platform": "agent",
                "from_user_id": self.session_id[:8],
                "from_user_name": self.agent_name,
                "composite_chat_id": f"agent:{self.session_id[:8]}",
                "content": text,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "msg_type": "agent_response",
                "source_session": self.session_id,
            }
            inbox_key = f"bridge:inbox:{self.reply_to}"
            notify_key = f"bridge:watcher:notify:{self.reply_to}"
            self._redis.rpush(inbox_key, json.dumps(msg, default=str))
            self._redis.rpush(notify_key, "1")
            logger.info(f"Response sent to agent inbox: {self.reply_to[:8]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to send to agent inbox: {e}")
            return False

    def _requeue_messages(self, messages: list[dict]) -> None:
        """Re-queue messages back to inbox if processing fails."""
        inbox_key = f"bridge:inbox:{self.session_id}"
        for msg in messages:
            self._redis.rpush(inbox_key, json.dumps(msg, default=str))

    def run(self) -> None:
        """Main daemon loop."""
        self._write_pid()
        self._running = True
        notify_key = f"bridge:watcher:notify:{self.session_id}"

        # Register signal handlers for graceful shutdown
        def _handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, shutting down")
            self._running = False

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        logger.info(f"Watcher daemon started: session={self.session_id} pid={os.getpid()}")

        try:
            self._connect_redis()
        except Exception as e:
            logger.error(f"Cannot connect to Redis: {e}")
            self._cleanup_pid()
            return

        try:
            while self._running:
                # Check failure thresholds
                if self._redis_failures >= MAX_REDIS_FAILURES:
                    logger.error(f"Too many Redis failures ({self._redis_failures}), exiting")
                    break
                if self._claude_failures >= MAX_CLAUDE_FAILURES:
                    logger.error(f"Too many agent CLI failures ({self._claude_failures}), exiting")
                    break

                # 1. BLPOP — wait for notify signal
                try:
                    result = self._redis.blpop(notify_key, timeout=self.blpop_timeout)
                except Exception as e:
                    logger.warning(f"Redis BLPOP error: {e}")
                    self._redis_failures += 1
                    time.sleep(min(2 ** self._redis_failures, 30))
                    try:
                        self._connect_redis()
                    except Exception:
                        pass
                    continue

                if result is None:
                    # Timeout — no messages, loop back
                    continue

                # 2. Check if interactive session is active — defer if so
                if self._is_interactive_active():
                    logger.debug("Interactive session active, deferring")
                    continue

                # 3. Batch delay — wait for more messages to coalesce
                time.sleep(self.batch_delay)

                # 4. Acquire lock
                if not self._acquire_lock():
                    logger.debug("Lock held by another watcher, skipping")
                    continue

                try:
                    # 5. Re-check interactive (may have become active during batch delay)
                    if self._is_interactive_active():
                        logger.debug("Interactive session became active during batch delay")
                        continue

                    # 6. Consume inbox
                    messages = self._consume_inbox()
                    if not messages:
                        self._drain_notify_queue()
                        continue

                    # 7. Drain remaining notify signals
                    self._drain_notify_queue()

                    # 8. Verify session JSONL exists (skip in no-resume mode)
                    if not self.no_resume:
                        jsonl = _find_session_jsonl(self.session_id)
                        if not jsonl:
                            logger.warning(f"Session JSONL not found for {self.session_id[:8]}, "
                                           "re-queuing messages")
                            self._requeue_messages(messages)
                            self._send_response(
                                "Agent session expired. Please start a new session."
                            )
                            self._running = False
                            break

                    # 9. Format and invoke the agent CLI
                    prompt = self._format_prompt(messages)
                    response = self._invoke_claude(prompt)

                    if response:
                        # 10. Route response to external chat
                        self._send_response(response)
                    else:
                        # Failed — re-queue messages for next attempt
                        logger.warning("Agent CLI invocation failed, re-queuing messages")
                        self._requeue_messages(messages)
                        # Re-push notify so we retry
                        self._redis.rpush(notify_key, "1")

                finally:
                    self._release_lock()

                # 11. Cooldown before next cycle
                time.sleep(self.cooldown)

        except Exception as e:
            logger.error(f"Unexpected error in watcher loop: {e}", exc_info=True)
        finally:
            self._cleanup_pid()
            logger.info("Watcher daemon stopped")


def main():
    parser = argparse.ArgumentParser(
        description="Bridge Watcher Daemon — auto-respond to external chat when the agent is idle"
    )
    parser.add_argument(
        "--session-id",
        help="Agent session ID. Auto-discovers from /tmp/bridge_current_session.txt if omitted."
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)"
    )
    parser.add_argument(
        "--config",
        help="Path to watcher_config.json (default: auto-detected)"
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Don't use --resume (standalone agent, no existing session JSONL)"
    )
    parser.add_argument(
        "--reply-to",
        help="Session ID to route responses back to (agent-to-agent mode)"
    )
    parser.add_argument(
        "--agent-name",
        default="Agent B",
        help="Display name for this agent in messages (default: Agent B)"
    )
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Discover session ID
    session_id = args.session_id or _discover_session_id()
    if not session_id:
        logger.error(
            "No session ID provided and none found at /tmp/bridge_current_session.txt.\n"
            "Either pass --session-id or send a message in the agent CLI first."
        )
        sys.exit(1)

    # Load config
    config = None
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            config = json.loads(config_path.read_text())

    # Verify session exists (skip in no-resume mode)
    if not args.no_resume:
        jsonl = _find_session_jsonl(session_id)
        if not jsonl:
            logger.warning(f"Session JSONL not found for {session_id[:8]}... — will check again on first message")

    mode = "no-resume" if args.no_resume else "resume"
    logger.info(f"Session: {session_id} (mode={mode})")
    logger.info(f"Redis: {REDIS_URL}")
    logger.info(f"Gateway: {GATEWAY_URL}")
    if args.reply_to:
        logger.info(f"Reply-to: {args.reply_to}")

    # Run daemon
    daemon = WatcherDaemon(
        session_id, config,
        no_resume=args.no_resume,
        reply_to=args.reply_to,
        agent_name=args.agent_name,
    )
    daemon.run()


if __name__ == "__main__":
    main()
