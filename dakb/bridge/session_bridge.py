"""Bridge Client -- runs as subprocess, connects WS to Gateway, buffers to Redis."""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BACKOFF = 30  # seconds


class SessionBridgeClient:
    """Local bridge client: WS -> Redis inbox with heartbeat and zombie prevention."""

    def __init__(
        self,
        session_id: str,
        gateway_url: str,
        redis_url: str = "redis://localhost:6379",
        pid_dir: str = "/tmp",
        fallback_dir: str = "/tmp",
        parent_pid: int | None = None,
    ):
        self.session_id = session_id
        self.gateway_url = gateway_url.rstrip("/")
        self.redis_url = redis_url
        self._pid_dir = Path(pid_dir)
        self._fallback_dir = Path(fallback_dir)
        self._parent_pid = parent_pid
        self._running = False
        self._redis = None

    def _write_pid(self) -> None:
        pid_file = self._pid_dir / f"bridge_{self.session_id}.pid"
        pid_file.write_text(str(os.getpid()))

    def _cleanup_pid(self) -> None:
        pid_file = self._pid_dir / f"bridge_{self.session_id}.pid"
        if pid_file.exists():
            pid_file.unlink()

    def _is_parent_alive(self) -> bool:
        """Check if parent process is still running. Returns True if no parent_pid set."""
        if self._parent_pid is None:
            return True
        try:
            os.kill(self._parent_pid, 0)
            return True
        except OSError:
            return False

    def _enqueue_to_file(self, msg: dict) -> None:
        """Degraded: append message to fallback file when Redis is down."""
        fallback = self._fallback_dir / f"bridge_inbox_{self.session_id}.jsonl"
        with fallback.open("a") as f:
            f.write(json.dumps(msg, default=str) + "\n")

    @staticmethod
    def _backoff_delay(attempt: int) -> int:
        """Exponential backoff: 1, 2, 4, 8, 16, 30, 30, ..."""
        return min(2 ** attempt, MAX_BACKOFF)

    async def _enqueue_to_redis(self, msg: dict) -> None:
        """Enqueue message to Redis inbox with file fallback."""
        msg_json = json.dumps(msg, default=str)
        inbox_key = f"bridge:inbox:{self.session_id}"
        try:
            await self._redis.rpush(inbox_key, msg_json)
            await self._redis.ltrim(inbox_key, -500, -1)
            # Notify watcher daemon that a new message is available
            await self._redis.rpush(f"bridge:watcher:notify:{self.session_id}", "1")
        except Exception:
            logger.warning("Redis unavailable, falling back to file")
            self._enqueue_to_file(msg)

    async def _update_heartbeat(self) -> None:
        """Update heartbeat in Redis."""
        try:
            key = f"bridge:heartbeat:{self.session_id}"
            await self._redis.set(key, str(time.time()), ex=60)
        except Exception:
            pass  # Best effort

    async def run(self) -> None:
        """Main run loop: connect WS, receive messages, push to Redis."""
        import redis.asyncio as aioredis
        import websockets

        self._write_pid()
        self._running = True
        self._redis = aioredis.from_url(self.redis_url)
        attempt = 0

        try:
            while self._running:
                if not self._is_parent_alive():
                    logger.warning("Parent process died, shutting down bridge")
                    break

                base = self.gateway_url.replace("http://", "ws://").replace("https://", "wss://")
                ws_url = f"{base}/ws/bridge/{self.session_id}"
                try:
                    async with websockets.connect(ws_url) as ws:
                        attempt = 0
                        logger.info(f"Bridge connected: {self.session_id}")
                        await self._update_heartbeat()

                        async for raw in ws:
                            if not self._is_parent_alive():
                                logger.warning("Parent died mid-session")
                                self._running = False
                                break

                            try:
                                msg = json.loads(raw)
                                await self._enqueue_to_redis(msg)
                            except json.JSONDecodeError:
                                logger.warning(f"Bad WS message: {raw!r}")

                            await self._update_heartbeat()

                except Exception as e:
                    if not self._running:
                        break
                    delay = self._backoff_delay(attempt)
                    logger.warning(f"WS error ({e}), reconnecting in {delay}s")
                    attempt += 1
                    await asyncio.sleep(delay)
        finally:
            self._cleanup_pid()
            if self._redis:
                try:
                    await self._redis.delete(f"bridge:heartbeat:{self.session_id}")
                except Exception:
                    pass
                await self._redis.aclose()

    def stop(self) -> None:
        """Signal the bridge to stop."""
        self._running = False
