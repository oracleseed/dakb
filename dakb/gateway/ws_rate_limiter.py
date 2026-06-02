"""
DAKB WebSocket Rate Limiter

Per-connection sliding window rate limiter for WebSocket messages.

Version: 1.0
"""

import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_MSG_PER_SEC = 10
DEFAULT_MAX_PAYLOAD_BYTES = 65536  # 64 KB
DEFAULT_MAX_HEARTBEAT_PER_MIN = 3


class WSRateLimiter:
    """
    Sliding window rate limiter for WebSocket connections.

    Tracks message timestamps per token_id using a deque.
    Messages outside the 1-second window are pruned on each check.
    """

    def __init__(
        self,
        max_messages_per_second: int = DEFAULT_MAX_MSG_PER_SEC,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ):
        self.max_mps = max_messages_per_second
        self.max_payload = max_payload_bytes
        self._windows: dict[str, deque] = defaultdict(deque)

    def check(self, token_id: str) -> bool:
        """
        Check if the token is allowed to send a message.

        Returns True if allowed, False if rate limited.
        """
        now = time.time()
        window = self._windows[token_id]

        # Prune timestamps older than 1 second
        while window and window[0] < now - 1.0:
            window.popleft()

        if len(window) >= self.max_mps:
            logger.warning("Rate limited: %s (%d msg/s)", token_id, len(window))
            return False

        window.append(now)
        return True

    def check_payload_size(self, payload: str) -> bool:
        """Check if a payload is within the size limit."""
        return len(payload.encode("utf-8")) <= self.max_payload

    def cleanup(self, token_id: str) -> None:
        """Remove rate limiter state for a disconnected token."""
        self._windows.pop(token_id, None)
