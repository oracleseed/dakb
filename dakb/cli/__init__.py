"""
DAKB Server CLI - Command Line Interface for DAKB services.

Usage:
    dakb-server init        Initialize configuration and secrets
    dakb-server start       Start all DAKB services
    dakb-server stop        Stop all DAKB services
    dakb-server status      Check service status
    dakb-server version     Show version information
"""

import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

import click

from dakb import __version__

# Default configuration
DEFAULT_CONFIG_DIR = Path.home() / ".dakb"
DEFAULT_GATEWAY_PORT = 3100
DEFAULT_EMBEDDING_PORT = 3101
PID_DIR = DEFAULT_CONFIG_DIR / "pids"


def get_pid_file(service: str) -> Path:
    """Get PID file path for a service."""
    return PID_DIR / f"{service}.pid"


def read_pid(service: str) -> int | None:
    """Read PID from file."""
    pid_file = get_pid_file(service)
    if pid_file.exists():
        try:
            return int(pid_file.read_text().strip())
        except (ValueError, OSError):
            return None
    return None


def write_pid(service: str, pid: int) -> None:
    """Write PID to file."""
    PID_DIR.mkdir(parents=True, exist_ok=True)
    get_pid_file(service).write_text(str(pid))


def remove_pid(service: str) -> None:
    """Remove PID file."""
    pid_file = get_pid_file(service)
    if pid_file.exists():
        pid_file.unlink()


def is_process_running(pid: int) -> bool:
    """Check if a process is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def check_mongo_connection(uri: str) -> bool:
    """Test MongoDB connectivity."""
    try:
        from pymongo import MongoClient
        from pymongo.errors import ConnectionFailure

        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        client.close()
        return True
    except (ConnectionFailure, Exception):
        return False


@click.group()
@click.version_option(version=__version__, prog_name="dakb-server")
def main():
    """DAKB Server - Distributed Agent Knowledge Base.

    A shared knowledge system for multi-agent AI collaboration.
    """
    pass


@main.command()
@click.option(
    "--config-dir",
    type=click.Path(),
    default=str(DEFAULT_CONFIG_DIR),
    help=f"Configuration directory (default: {DEFAULT_CONFIG_DIR})",
)
@click.option(
    "--mongo-uri",
    type=str,
    default=None,
    help="MongoDB connection URI (default: mongodb://localhost:27017)",
)
@click.option(
    "--skip-mongo-check",
    is_flag=True,
    default=False,
    help="Skip MongoDB connectivity test",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing configuration",
)
def init(config_dir: str, mongo_uri: str | None, skip_mongo_check: bool, force: bool):
    """Initialize DAKB configuration and secrets.

    This command will:
    - Create configuration directory
    - Generate security secrets
    - Create .env file with settings
    - Test MongoDB connectivity (unless --skip-mongo-check)
    - Create FAISS data directory
    """
    config_path = Path(config_dir)
    env_file = config_path / ".env"
    data_dir = config_path / "data"
    faiss_dir = data_dir / "faiss"

    click.echo(f"\n🚀 Initializing DAKB Server v{__version__}")
    click.echo("=" * 50)

    # Check if already initialized
    if env_file.exists() and not force:
        click.echo(f"\n⚠️  Configuration already exists at {config_path}")
        click.echo("   Use --force to overwrite existing configuration")
        sys.exit(1)

    # Create directories
    click.echo("\n📁 Creating directories...")
    config_path.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    faiss_dir.mkdir(parents=True, exist_ok=True)
    PID_DIR.mkdir(parents=True, exist_ok=True)
    click.echo(f"   ✓ Config: {config_path}")
    click.echo(f"   ✓ Data: {data_dir}")
    click.echo(f"   ✓ FAISS: {faiss_dir}")

    # Generate secrets
    click.echo("\n🔐 Generating security secrets...")
    internal_secret = secrets.token_hex(32)
    jwt_secret = secrets.token_hex(32)
    click.echo("   ✓ DAKB_INTERNAL_SECRET generated")
    click.echo("   ✓ DAKB_JWT_SECRET generated")

    # Default MongoDB URI
    if mongo_uri is None:
        mongo_uri = "mongodb://localhost:27017"

    # Test MongoDB connection
    if not skip_mongo_check:
        click.echo("\n🔗 Testing MongoDB connection...")
        if check_mongo_connection(mongo_uri):
            click.echo("   ✓ Connected to MongoDB")
        else:
            click.echo(f"   ⚠️  Could not connect to MongoDB at {mongo_uri}")
            click.echo("      Make sure MongoDB is running, or use --skip-mongo-check")
            if not click.confirm("   Continue anyway?", default=False):
                sys.exit(1)

    # Write .env file
    click.echo("\n📝 Writing configuration...")
    env_content = f"""# DAKB Server Configuration
# Generated by dakb-server init

# MongoDB Connection
MONGO_URI={mongo_uri}
DAKB_DATABASE=dakb_db

# Security Secrets (DO NOT SHARE)
DAKB_INTERNAL_SECRET={internal_secret}
DAKB_JWT_SECRET={jwt_secret}

# Service Ports
DAKB_GATEWAY_PORT={DEFAULT_GATEWAY_PORT}
DAKB_EMBEDDING_PORT={DEFAULT_EMBEDDING_PORT}

# Data Directories
DAKB_DATA_DIR={data_dir}
DAKB_FAISS_DIR={faiss_dir}

# Embedding Model (default: all-MiniLM-L6-v2)
DAKB_EMBEDDING_MODEL=all-MiniLM-L6-v2
DAKB_EMBEDDING_DEVICE=cpu
"""
    env_file.write_text(env_content)
    click.echo(f"   ✓ Configuration saved to {env_file}")

    # Success message
    click.echo("\n" + "=" * 50)
    click.echo("✅ DAKB Server initialized successfully!")
    click.echo("\n📋 Next steps:")
    click.echo(f"   1. Review configuration: {env_file}")
    click.echo("   2. Start services: dakb-server start")
    click.echo("   3. Check status: dakb-server status")
    click.echo("\n💡 Tip: Source the .env file before starting:")
    click.echo(f"   source {env_file}")
    click.echo()


@main.command()
@click.option(
    "--gateway-port",
    type=int,
    default=DEFAULT_GATEWAY_PORT,
    help=f"Gateway port (default: {DEFAULT_GATEWAY_PORT})",
)
@click.option(
    "--embedding-port",
    type=int,
    default=DEFAULT_EMBEDDING_PORT,
    help=f"Embedding service port (default: {DEFAULT_EMBEDDING_PORT})",
)
@click.option(
    "--foreground",
    is_flag=True,
    default=False,
    help="Run in foreground (don't daemonize)",
)
@click.option(
    "--config-dir",
    type=click.Path(),
    default=str(DEFAULT_CONFIG_DIR),
    help=f"Configuration directory (default: {DEFAULT_CONFIG_DIR})",
)
def start(gateway_port: int, embedding_port: int, foreground: bool, config_dir: str):
    """Start DAKB services (embedding and gateway).

    Starts both the embedding service and gateway service.
    Use --foreground to run in the current terminal.
    """
    config_path = Path(config_dir)
    env_file = config_path / ".env"

    click.echo(f"\n🚀 Starting DAKB Server v{__version__}")
    click.echo("=" * 50)

    # Load environment from .env if exists
    if env_file.exists():
        click.echo(f"📁 Loading config from {env_file}")
        from dotenv import load_dotenv
        load_dotenv(env_file)

    # Check if services are already running
    embedding_pid = read_pid("embedding")
    gateway_pid = read_pid("gateway")

    if embedding_pid and is_process_running(embedding_pid):
        click.echo(f"⚠️  Embedding service already running (PID: {embedding_pid})")
    else:
        # Start embedding service
        click.echo(f"\n🔧 Starting embedding service on port {embedding_port}...")
        try:
            if foreground:
                # Run in foreground (blocking)
                from dakb.embeddings import run
                run()
            else:
                # Run as background process
                proc = subprocess.Popen(
                    [sys.executable, "-m", "dakb.embeddings"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                write_pid("embedding", proc.pid)
                time.sleep(2)  # Wait for startup
                if is_process_running(proc.pid):
                    click.echo(f"   ✓ Embedding service started (PID: {proc.pid})")
                else:
                    click.echo("   ❌ Embedding service failed to start")
                    sys.exit(1)
        except Exception as e:
            click.echo(f"   ❌ Failed to start embedding service: {e}")
            sys.exit(1)

    if gateway_pid and is_process_running(gateway_pid):
        click.echo(f"⚠️  Gateway service already running (PID: {gateway_pid})")
    else:
        # Start gateway service
        click.echo(f"\n🌐 Starting gateway service on port {gateway_port}...")
        try:
            if foreground:
                click.echo("   (foreground mode - embedding must be started separately)")
            else:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "dakb.gateway"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                write_pid("gateway", proc.pid)
                time.sleep(2)  # Wait for startup
                if is_process_running(proc.pid):
                    click.echo(f"   ✓ Gateway service started (PID: {proc.pid})")
                else:
                    click.echo("   ❌ Gateway service failed to start")
                    sys.exit(1)
        except Exception as e:
            click.echo(f"   ❌ Failed to start gateway service: {e}")
            sys.exit(1)

    click.echo("\n" + "=" * 50)
    click.echo("✅ DAKB Server started!")
    click.echo(f"\n🔗 Gateway URL: http://localhost:{gateway_port}")
    click.echo(f"📊 Health check: http://localhost:{gateway_port}/health")
    click.echo("\n💡 Use 'dakb-server status' to check service status")
    click.echo("💡 Use 'dakb-server stop' to stop services")
    click.echo()


@main.command()
def stop():
    """Stop all DAKB services."""
    click.echo("\n🛑 Stopping DAKB Server")
    click.echo("=" * 50)

    services_stopped = 0

    for service in ["gateway", "embedding"]:
        pid = read_pid(service)
        if pid and is_process_running(pid):
            click.echo(f"\n🔧 Stopping {service} service (PID: {pid})...")
            try:
                os.kill(pid, signal.SIGTERM)
                # Wait for graceful shutdown
                for _ in range(10):
                    if not is_process_running(pid):
                        break
                    time.sleep(0.5)

                if is_process_running(pid):
                    # Force kill
                    os.kill(pid, signal.SIGKILL)
                    time.sleep(0.5)

                remove_pid(service)
                click.echo(f"   ✓ {service.capitalize()} service stopped")
                services_stopped += 1
            except Exception as e:
                click.echo(f"   ❌ Failed to stop {service}: {e}")
        else:
            click.echo(f"\n⚠️  {service.capitalize()} service not running")
            remove_pid(service)  # Clean up stale PID file

    click.echo("\n" + "=" * 50)
    if services_stopped > 0:
        click.echo(f"✅ Stopped {services_stopped} service(s)")
    else:
        click.echo("ℹ️  No services were running")
    click.echo()


@main.command()
@click.option(
    "--config-dir",
    type=click.Path(),
    default=str(DEFAULT_CONFIG_DIR),
    help=f"Configuration directory (default: {DEFAULT_CONFIG_DIR})",
)
def status(config_dir: str):
    """Check status of DAKB services."""
    config_path = Path(config_dir)
    env_file = config_path / ".env"

    click.echo("\n📊 DAKB Server Status")
    click.echo("=" * 50)

    # Configuration status
    click.echo("\n📁 Configuration:")
    if env_file.exists():
        click.echo(f"   ✓ Config file: {env_file}")
    else:
        click.echo("   ⚠️  Not initialized (run 'dakb-server init')")

    # Service status
    click.echo("\n🔧 Services:")

    services = {
        "embedding": {"port": DEFAULT_EMBEDDING_PORT, "status": "stopped"},
        "gateway": {"port": DEFAULT_GATEWAY_PORT, "status": "stopped"},
    }

    for service, info in services.items():
        pid = read_pid(service)
        if pid and is_process_running(pid):
            info["status"] = "running"
            info["pid"] = pid
            status_icon = "✅"
            status_text = f"running (PID: {pid})"
        else:
            status_icon = "❌"
            status_text = "stopped"
            if pid:
                remove_pid(service)  # Clean up stale PID

        click.echo(f"   {status_icon} {service.capitalize():12} {status_text}")

    # MongoDB status
    click.echo("\n🗄️  MongoDB:")
    mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    if check_mongo_connection(mongo_uri):
        click.echo("   ✓ Connected")
    else:
        click.echo(f"   ❌ Not connected ({mongo_uri})")

    click.echo("\n" + "=" * 50)

    # Overall status
    all_running = all(
        read_pid(s) and is_process_running(read_pid(s))
        for s in services
    )
    if all_running:
        click.echo("✅ All services running")
    else:
        click.echo("⚠️  Some services not running")
    click.echo()


@main.command()
def version():
    """Show version information."""
    click.echo(f"\n📦 DAKB Server v{__version__}")
    click.echo("=" * 50)
    click.echo("   Package: dakb-server")
    click.echo(f"   Version: {__version__}")
    click.echo(f"   Python:  {sys.version.split()[0]}")
    click.echo("   License: Apache-2.0")
    click.echo("\n🔗 https://github.com/oracleseed/dakb")
    click.echo()


if __name__ == "__main__":
    main()
