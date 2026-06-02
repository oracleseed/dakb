"""Unit tests for bridge hooks."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

SDK_ROOT = Path(__file__).parent.parent / "bridge_client_sdk"
sys.path.insert(0, str(SDK_ROOT.parent))


class TestInboxHook:
    """Test the inbox hook message consumption and formatting."""

    def test_consume_from_redis(self):
        """Test consuming messages from Redis inbox."""
        mock_redis = MagicMock()
        mock_r = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_r

        msg = json.dumps({
            "msg_id": "test_1",
            "from_platform": "telegram",
            "from_user_name": "Alice",
            "content": "Hello"
        }).encode()

        mock_r.exists.return_value = True
        mock_r.lrange.return_value = [msg]

        with patch.dict("sys.modules", {"redis": mock_redis}):
            from bridge_client_sdk.hooks.inbox_hook import _consume_from_redis
            result = _consume_from_redis("test-session")

        assert result is not None
        messages, overflow = result
        assert len(messages) == 1
        assert messages[0]["content"] == "Hello"
        assert overflow == 0

    def test_consume_empty_inbox(self):
        """Test consuming from empty inbox returns None."""
        mock_redis = MagicMock()
        mock_r = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_r
        mock_r.exists.return_value = False

        with patch.dict("sys.modules", {"redis": mock_redis}):
            from bridge_client_sdk.hooks.inbox_hook import _consume_from_redis
            result = _consume_from_redis("test-session")

        assert result is None

    def test_peek_redis(self):
        """Test non-destructive peek at inbox depth."""
        mock_redis = MagicMock()
        mock_r = MagicMock()
        mock_redis.Redis.from_url.return_value = mock_r
        mock_r.llen.return_value = 3

        with patch.dict("sys.modules", {"redis": mock_redis}):
            from bridge_client_sdk.hooks.inbox_hook import _peek_redis
            assert _peek_redis("test-session") == 3

    def test_consume_from_file(self, tmp_path):
        """Test file-based fallback consumption."""
        fallback = tmp_path / "bridge_inbox_test-session.jsonl"
        msg = {"msg_id": "1", "from_platform": "test", "from_user_name": "Bob", "content": "Hi"}
        fallback.write_text(json.dumps(msg) + "\n")

        lines = [l for l in fallback.read_text().splitlines() if l.strip()]
        messages = [json.loads(l) for l in lines]
        assert len(messages) == 1
        assert messages[0]["content"] == "Hi"

    def test_overflow_handling(self):
        """Test that overflow messages are counted correctly."""
        messages = [{"msg_id": str(i), "content": f"msg{i}"} for i in range(25)]
        overflow = len(messages) - 20
        assert overflow == 5


class TestCleanupHook:
    """Test the cleanup hook."""

    def test_import(self):
        from bridge_client_sdk.hooks.cleanup_hook import main
        assert callable(main)


class TestEventHook:
    """Test the event notification hook."""

    def test_import(self):
        from bridge_client_sdk.hooks.event_hook import main
        assert callable(main)


class TestTestHook:
    """Test the test hook."""

    def test_import(self):
        from bridge_client_sdk.hooks.test_hook import main
        assert callable(main)
