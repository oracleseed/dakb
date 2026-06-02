"""Integration tests for the Bridge Client SDK.

These tests verify end-to-end flows with mocked Redis.
Install in editable mode for development:
    pip install -e dakb/bridge/Client_SDK[dev]
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

SDK_ROOT = Path(__file__).parent.parent / "bridge_client_sdk"
sys.path.insert(0, str(SDK_ROOT.parent))


def _find_repo_root() -> Path | None:
    """Auto-detect the repo root that contains the importable ``dakb`` package.

    No hard-coded absolute/home paths: walk up from this test file until a
    ``dakb`` package is found.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "dakb" / "__init__.py").exists():
            return parent
    return None


class TestSDKPackage:
    """Verify SDK package structure and imports."""

    def test_package_import(self):
        import bridge_client_sdk
        assert bridge_client_sdk.__version__ == "0.1.0"

    def test_hooks_import(self):
        from bridge_client_sdk.hooks import cleanup_hook, event_hook, inbox_hook, test_hook
        assert all(hasattr(m, "main") for m in [inbox_hook, cleanup_hook, event_hook, test_hook])

    def test_scripts_import(self):
        from bridge_client_sdk import client_launcher, client_runner, test_fire, watcher_daemon
        assert all(hasattr(m, "main") for m in [client_launcher, client_runner, test_fire, watcher_daemon])

    def test_config_exists(self):
        config_path = Path(__file__).parent.parent / "bridge_client_sdk" / "config" / "watcher_config.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())
        assert "blpop_timeout" in config
        assert "batch_delay" in config
        assert "max_budget_usd" in config


class TestSDKEntryPoints:
    """Verify the SDK declares its console-script entry points."""

    def test_entry_points_declared(self):
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject.read_text()
        for script in ("bridge-watcher", "bridge-launcher", "bridge-test-fire", "bridge-relay"):
            assert script in content, f"Missing entry point: {script}"


class TestNotificationKeys:
    """Verify notification keys are present in server-side bridge code."""

    def test_session_bridge_has_notify(self):
        """session_bridge.py should push to watcher notify key."""
        repo_root = _find_repo_root()
        assert repo_root is not None, "Could not locate the dakb package root"
        code = (repo_root / "dakb" / "bridge" / "session_bridge.py").read_text()
        assert "bridge:watcher:notify:" in code

    def test_queue_has_notify(self):
        """queue.py should push to watcher notify key."""
        repo_root = _find_repo_root()
        assert repo_root is not None, "Could not locate the dakb package root"
        code = (repo_root / "dakb" / "bridge" / "queue.py").read_text()
        assert "bridge:watcher:notify:" in code


class TestDualResponsePrevention:
    """Verify the dual-response prevention mechanism."""

    def test_inbox_hook_has_interactive_signal(self):
        """inbox_hook.py should set bridge:interactive: key."""
        import inspect

        from bridge_client_sdk.hooks import inbox_hook
        source = inspect.getsource(inbox_hook)
        assert "bridge:interactive:" in source

    def test_inbox_hook_checks_watcher_lock(self):
        """inbox_hook.py should check bridge:watcher:lock: key."""
        import inspect

        from bridge_client_sdk.hooks import inbox_hook
        source = inspect.getsource(inbox_hook)
        assert "bridge:watcher:lock:" in source

    def test_watcher_checks_interactive(self):
        """watcher_daemon.py should check bridge:interactive: key."""
        import inspect

        from bridge_client_sdk import watcher_daemon
        source = inspect.getsource(watcher_daemon)
        assert "bridge:interactive:" in source

    def test_watcher_sets_lock(self):
        """watcher_daemon.py should set bridge:watcher:lock: key."""
        import inspect

        from bridge_client_sdk import watcher_daemon
        source = inspect.getsource(watcher_daemon)
        assert "bridge:watcher:lock:" in source


class TestAgentLaunchGating:
    """Verify agent-launch-from-chat is denied by default."""

    def test_launch_disabled_without_env_flag(self, monkeypatch):
        """validate_launch must deny when the global env flag is unset."""
        import asyncio

        from bridge_client_sdk import watcher_daemon  # noqa: F401 (ensure SDK path set)
        sys.path.insert(0, str(SDK_ROOT.parent.parent.parent.parent))
        monkeypatch.delenv("DAKB_BRIDGE_ALLOW_AGENT_LAUNCH", raising=False)

        from dakb.bridge.launcher import AgentLauncher, agent_launch_globally_enabled
        from dakb.bridge.models import LaunchConfig

        assert agent_launch_globally_enabled() is False

        launcher = AgentLauncher(MagicMock())
        config = LaunchConfig(
            agent_id="x",
            allowed_users=[123],
            launch_template="{session_id} {escaped_message}",
            enabled=True,
        )
        ok, err = asyncio.run(launcher.validate_launch(123, config))
        assert ok is False
        assert "disabled" in err.lower()


class TestWatcherDaemonFlow:
    """Test the watcher daemon's main flow with mocked dependencies."""

    def test_daemon_defers_when_interactive(self):
        """Daemon should skip processing when interactive session is active."""
        from bridge_client_sdk.watcher_daemon import WatcherDaemon

        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()
        daemon._redis.exists.return_value = 1  # interactive key exists

        assert daemon._is_interactive_active() is True

    def test_daemon_lock_prevents_hook(self):
        """When daemon holds lock, inbox hook should detect it."""
        from bridge_client_sdk.watcher_daemon import WatcherDaemon

        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()

        # Acquire lock
        daemon._redis.set.return_value = True
        assert daemon._acquire_lock() is True

        # Verify lock key pattern (PID is str(os.getpid()))
        call_args = daemon._redis.set.call_args
        assert call_args[0][0] == "bridge:watcher:lock:test-session"
        assert call_args[1]["nx"] is True
        assert call_args[1]["ex"] == 120

    def test_message_batching(self):
        """Multiple messages should be consumed as a batch."""
        from bridge_client_sdk.watcher_daemon import WatcherDaemon

        daemon = WatcherDaemon("test-session")
        daemon._redis = MagicMock()

        msgs = [
            json.dumps({"from_platform": "tg", "from_user_name": "A", "content": f"msg{i}"})
            for i in range(5)
        ]
        pipe = MagicMock()
        pipe.execute.return_value = [msgs, True]
        daemon._redis.pipeline.return_value = pipe

        consumed = daemon._consume_inbox()
        assert len(consumed) == 5
