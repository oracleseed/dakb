"""Unit tests for the watcher daemon."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add SDK to path
SDK_ROOT = Path(__file__).parent.parent / "bridge_client_sdk"
sys.path.insert(0, str(SDK_ROOT.parent))

from bridge_client_sdk.watcher_daemon import WatcherDaemon, _discover_session_id


class TestWatcherDaemonInit:
    """Test daemon initialization and configuration."""

    def test_default_config(self):
        daemon = WatcherDaemon("test-session-123")
        assert daemon.session_id == "test-session-123"
        assert daemon.blpop_timeout == 30
        assert daemon.batch_delay == 2.0
        assert daemon.cooldown == 5.0
        assert daemon.lock_ttl == 120
        assert daemon.max_budget == 0.50

    def test_custom_config(self):
        config = {
            "blpop_timeout": 10,
            "batch_delay": 1.0,
            "cooldown": 3.0,
            "lock_ttl": 60,
            "max_budget_usd": 1.00,
        }
        daemon = WatcherDaemon("test-session-123", config)
        assert daemon.blpop_timeout == 10
        assert daemon.batch_delay == 1.0
        assert daemon.cooldown == 3.0
        assert daemon.lock_ttl == 60
        assert daemon.max_budget == 1.00

    def test_pid_file_operations(self, tmp_path):
        daemon = WatcherDaemon("test-session-123")
        # Override PID dir
        import bridge_client_sdk.watcher_daemon as wd
        original_pid_dir = wd.PID_DIR
        wd.PID_DIR = tmp_path

        try:
            daemon._write_pid()
            pid_file = tmp_path / "bridge_watcher_test-session-123.pid"
            assert pid_file.exists()
            assert pid_file.read_text() == str(os.getpid())

            daemon._cleanup_pid()
            assert not pid_file.exists()
        finally:
            wd.PID_DIR = original_pid_dir


class TestWatcherDaemonFormatPrompt:
    """Test message formatting for the agent CLI."""

    def test_single_message(self):
        daemon = WatcherDaemon("test-session")
        messages = [{
            "from_platform": "telegram",
            "from_user_name": "Alice",
            "content": "Hello, can you check the status?"
        }]
        prompt = daemon._format_prompt(messages)
        assert "[telegram] Alice: Hello, can you check the status?" in prompt
        assert "External chat messages" in prompt

    def test_multiple_messages(self):
        daemon = WatcherDaemon("test-session")
        messages = [
            {"from_platform": "telegram", "from_user_name": "Alice", "content": "msg1"},
            {"from_platform": "discord", "from_user_name": "Bob", "content": "msg2"},
        ]
        prompt = daemon._format_prompt(messages)
        assert "[telegram] Alice: msg1" in prompt
        assert "[discord] Bob: msg2" in prompt

    def test_missing_fields(self):
        daemon = WatcherDaemon("test-session")
        messages = [{"content": "bare message"}]
        prompt = daemon._format_prompt(messages)
        assert "[unknown] Unknown: bare message" in prompt


class TestWatcherDaemonRedisOps:
    """Test Redis operations with mocked Redis client."""

    def test_is_interactive_active(self):
        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()
        daemon._redis.exists.return_value = 1
        assert daemon._is_interactive_active() is True

        daemon._redis.exists.return_value = 0
        assert daemon._is_interactive_active() is False

    def test_acquire_lock(self):
        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()

        daemon._redis.set.return_value = True
        assert daemon._acquire_lock() is True

        daemon._redis.set.return_value = None
        assert daemon._acquire_lock() is False

    def test_release_lock(self):
        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()
        daemon._release_lock()
        daemon._redis.delete.assert_called_once_with("bridge:watcher:lock:test-session")

    def test_consume_inbox(self):
        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()

        msg1 = json.dumps({"from_platform": "test", "content": "hello"})
        msg2 = json.dumps({"from_platform": "test", "content": "world"})

        pipe = MagicMock()
        pipe.execute.return_value = [[msg1, msg2], True]
        daemon._redis.pipeline.return_value = pipe

        messages = daemon._consume_inbox()
        assert len(messages) == 2
        assert messages[0]["content"] == "hello"
        assert messages[1]["content"] == "world"

    def test_consume_inbox_empty(self):
        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()

        pipe = MagicMock()
        pipe.execute.return_value = [[], True]
        daemon._redis.pipeline.return_value = pipe

        messages = daemon._consume_inbox()
        assert messages == []

    def test_drain_notify_queue(self):
        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()
        daemon._drain_notify_queue()
        daemon._redis.delete.assert_called_once_with("bridge:watcher:notify:test-session")

    def test_requeue_messages(self):
        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()
        messages = [
            {"from_platform": "test", "content": "msg1"},
            {"from_platform": "test", "content": "msg2"},
        ]
        daemon._requeue_messages(messages)
        assert daemon._redis.rpush.call_count == 2


class TestSessionDiscovery:
    """Test session ID discovery."""

    def test_discover_from_file(self, tmp_path):
        import bridge_client_sdk.watcher_daemon as wd
        original = wd.SESSION_FILE
        session_file = tmp_path / "bridge_current_session.txt"
        session_file.write_text("abc-123-def")
        wd.SESSION_FILE = session_file

        try:
            assert _discover_session_id() == "abc-123-def"
        finally:
            wd.SESSION_FILE = original

    def test_discover_missing_file(self, tmp_path):
        import bridge_client_sdk.watcher_daemon as wd
        original = wd.SESSION_FILE
        wd.SESSION_FILE = tmp_path / "nonexistent.txt"

        try:
            assert _discover_session_id() is None
        finally:
            wd.SESSION_FILE = original


class TestWatcherDaemonClaude:
    """Test agent CLI invocation (mocked subprocess)."""

    @patch("subprocess.run")
    def test_invoke_claude_success(self, mock_run):
        daemon = WatcherDaemon("test-session")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"result": "Hello! Everything is going well."}),
            stderr=""
        )

        result = daemon._invoke_claude("test prompt")
        assert result == "Hello! Everything is going well."
        assert daemon._claude_failures == 0

    @patch("subprocess.run")
    def test_invoke_claude_failure(self, mock_run):
        daemon = WatcherDaemon("test-session")
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: rate limited"
        )

        result = daemon._invoke_claude("test prompt")
        assert result is None
        assert daemon._claude_failures == 1

    @patch("subprocess.run")
    def test_invoke_claude_timeout(self, mock_run):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd="claude", timeout=300)
        daemon = WatcherDaemon("test-session")

        result = daemon._invoke_claude("test prompt")
        assert result is None
        assert daemon._claude_failures == 1

    @patch("subprocess.run")
    def test_invoke_claude_plain_text(self, mock_run):
        daemon = WatcherDaemon("test-session")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Plain text response",
            stderr=""
        )

        result = daemon._invoke_claude("test prompt")
        assert result == "Plain text response"


class TestWatcherDaemonSendResponse:
    """Test response routing via bridge API."""

    @patch("bridge_client_sdk.watcher_daemon.urlopen")
    def test_send_response_success(self, mock_urlopen):
        daemon = WatcherDaemon("test-session")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"delivered_to": 1}).encode()
        mock_urlopen.return_value = mock_resp

        result = daemon._send_response("Hello from daemon")
        assert result is True

    @patch("bridge_client_sdk.watcher_daemon.urlopen")
    def test_send_response_failure(self, mock_urlopen):
        daemon = WatcherDaemon("test-session")
        mock_urlopen.side_effect = Exception("Connection refused")

        result = daemon._send_response("Hello from daemon")
        assert result is False
