"""
DAKB Local Proxy CLI

Command line interface for managing the local proxy.

Version: 1.0.0
Created: 2025-12-17

Usage:
    dakb-proxy start [--port PORT] [--gateway URL]
    dakb-proxy stop
    dakb-proxy status
    dakb-proxy cache stats
    dakb-proxy cache clear
    dakb-proxy config show
    dakb-proxy config set KEY VALUE
"""

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from .config import ProxyConfig
from .proxy import DAKBLocalProxy, run_proxy


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="dakb-proxy",
        description="DAKB Local Proxy - MCP stdio transport to HTTP gateway",
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version="dakb-proxy 1.0.0",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Start command
    start_parser = subparsers.add_parser("start", help="Start the proxy server")
    start_parser.add_argument(
        "--gateway", "-g",
        help="DAKB gateway URL (default: http://localhost:3100)",
    )
    start_parser.add_argument(
        "--port", "-p",
        type=int,
        help="Local proxy port (default: 3110)",
    )
    start_parser.add_argument(
        "--token", "-t",
        help="Auth token (or set DAKB_AUTH_TOKEN)",
    )
    start_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable local caching",
    )
    start_parser.add_argument(
        "--log-level", "-l",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level",
    )
    start_parser.add_argument(
        "--log-file",
        help="Log to file instead of stderr",
    )

    # Stop command
    subparsers.add_parser("stop", help="Stop the proxy server")

    # Status command
    subparsers.add_parser("status", help="Check proxy status")

    # Cache commands
    cache_parser = subparsers.add_parser("cache", help="Cache management")
    cache_subparsers = cache_parser.add_subparsers(dest="cache_command")
    cache_subparsers.add_parser("stats", help="Show cache statistics")
    cache_subparsers.add_parser("clear", help="Clear all caches")

    # Config commands
    config_parser = subparsers.add_parser("config", help="Configuration management")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("show", help="Show current configuration")
    config_set = config_subparsers.add_parser("set", help="Set configuration value")
    config_set.add_argument("key", help="Configuration key (dot notation)")
    config_set.add_argument("value", help="Value to set")
    config_subparsers.add_parser("init", help="Create default configuration")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "start":
        cmd_start(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "cache":
        if args.cache_command == "stats":
            cmd_cache_stats(args)
        elif args.cache_command == "clear":
            cmd_cache_clear(args)
        else:
            cache_parser.print_help()
    elif args.command == "config":
        if args.config_command == "show":
            cmd_config_show(args)
        elif args.config_command == "set":
            cmd_config_set(args)
        elif args.config_command == "init":
            cmd_config_init(args)
        else:
            config_parser.print_help()


def cmd_start(args):
    """Start the proxy server."""
    config = ProxyConfig.load()

    # Apply CLI overrides
    if args.gateway:
        config.connection.gateway_url = args.gateway
    if args.port:
        config.server.port = args.port
    if args.token:
        config.auth_token = args.token
    if args.no_cache:
        config.cache.enabled = False
    if args.log_level:
        config.logging.level = args.log_level
    if args.log_file:
        config.logging.file = args.log_file

    # Validate
    errors = config.validate()
    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    config.setup_logging()

    print(f"Starting DAKB proxy...")
    print(f"  Gateway: {config.connection.gateway_url}")
    print(f"  Cache: {'enabled' if config.cache.enabled else 'disabled'}")
    print(f"  Log level: {config.logging.level}")
    print()

    # Write PID file
    pid_file = Path.home() / ".dakb" / "proxy.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    # Track if shutdown is in progress to avoid duplicate handling
    shutdown_initiated = False

    def handle_shutdown(signum, frame):
        """Handle graceful shutdown on SIGINT/SIGTERM."""
        nonlocal shutdown_initiated
        if shutdown_initiated:
            return
        shutdown_initiated = True
        sig_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
        print(f"\nReceived {sig_name}, shutting down gracefully...")
        # The asyncio.run() will handle cleanup via KeyboardInterrupt or CancelledError

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        asyncio.run(run_proxy(config))
    except KeyboardInterrupt:
        print("\nProxy shutdown complete.")
    finally:
        if pid_file.exists():
            pid_file.unlink()


def cmd_stop(args):
    """Stop the proxy server."""
    pid_file = Path.home() / ".dakb" / "proxy.pid"

    if not pid_file.exists():
        print("Proxy is not running (no PID file)")
        sys.exit(1)

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to proxy (PID {pid})")
        pid_file.unlink()
    except ProcessLookupError:
        print("Proxy process not found, cleaning up PID file")
        pid_file.unlink()
    except Exception as e:
        print(f"Error stopping proxy: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
    """Check proxy status."""
    pid_file = Path.home() / ".dakb" / "proxy.pid"

    if not pid_file.exists():
        print("Proxy status: NOT RUNNING")
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        print(f"Proxy status: RUNNING (PID {pid})")
    except ProcessLookupError:
        print("Proxy status: STALE (process not found, cleaning up)")
        pid_file.unlink()
    except Exception as e:
        print(f"Proxy status: UNKNOWN ({e})")


def cmd_cache_stats(args):
    """Show cache statistics."""
    # This would need to connect to running proxy
    # For now, just show config
    config = ProxyConfig.load()
    print("Cache Configuration:")
    print(f"  Enabled: {config.cache.enabled}")
    print(f"  Max entries: {config.cache.max_entries}")
    print(f"  TTL: {config.cache.ttl_seconds}s")
    print(f"  Search TTL: {config.cache.search_cache_ttl}s")
    print()
    print("Note: Runtime stats require connecting to running proxy")


def cmd_cache_clear(args):
    """Clear all caches. Only removes cache files, preserves directory structure."""
    cache_dir = Path.home() / ".dakb" / "cache"
    cleared_count = 0

    if cache_dir.exists():
        # Only remove cache files, not the entire directory
        cache_patterns = ["*.json", "*.cache", "*.pkl"]
        for pattern in cache_patterns:
            for cache_file in cache_dir.glob(pattern):
                try:
                    cache_file.unlink()
                    cleared_count += 1
                except OSError as e:
                    print(f"Warning: Could not remove {cache_file}: {e}", file=sys.stderr)

        if cleared_count > 0:
            print(f"Cleared {cleared_count} cache file(s)")
        else:
            print("No cache files found to clear")
    else:
        print("No cache directory found")


def cmd_config_show(args):
    """Show current configuration."""
    config = ProxyConfig.load()

    print("DAKB Proxy Configuration")
    print("=" * 40)
    print()
    print("[Connection]")
    print(f"  gateway_url: {config.connection.gateway_url}")
    print(f"  timeout: {config.connection.timeout_seconds}s")
    print(f"  max_retries: {config.connection.max_retries}")
    print()
    print("[Server]")
    print(f"  host: {config.server.host}")
    print(f"  port: {config.server.port}")
    print(f"  stdio_enabled: {config.server.stdio_enabled}")
    print(f"  http_enabled: {config.server.http_enabled}")
    print()
    print("[Cache]")
    print(f"  enabled: {config.cache.enabled}")
    print(f"  max_entries: {config.cache.max_entries}")
    print(f"  ttl_seconds: {config.cache.ttl_seconds}")
    print(f"  search_cache_ttl: {config.cache.search_cache_ttl}")
    print()
    print("[Logging]")
    print(f"  level: {config.logging.level}")
    print(f"  file: {config.logging.file or '(stderr)'}")
    print()
    print(f"Config file: {ProxyConfig.DEFAULT_CONFIG_FILE}")
    print(f"Auth token: {'SET' if config.auth_token else 'NOT SET'}")


def cmd_config_set(args):
    """Set configuration value."""
    config = ProxyConfig.load()

    key_parts = args.key.split(".")
    value = args.value

    # Parse value type
    if value.lower() == "true":
        value = True
    elif value.lower() == "false":
        value = False
    elif value.isdigit():
        value = int(value)
    elif value.replace(".", "", 1).isdigit():
        value = float(value)

    try:
        if len(key_parts) == 1:
            setattr(config, key_parts[0], value)
        elif len(key_parts) == 2:
            section = getattr(config, key_parts[0])
            setattr(section, key_parts[1], value)
        else:
            raise ValueError("Invalid key format")

        config.save()
        print(f"Set {args.key} = {value}")

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_config_init(args):
    """Create default configuration."""
    config = ProxyConfig()
    config.save()
    print(f"Created default configuration at {ProxyConfig.DEFAULT_CONFIG_FILE}")
    print()
    print("Don't forget to set your auth token:")
    print("  export DAKB_AUTH_TOKEN='your-token'")


if __name__ == "__main__":
    main()
