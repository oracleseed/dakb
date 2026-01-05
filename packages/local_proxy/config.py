"""
DAKB Local Proxy Configuration

Configuration management for the local proxy server.
Supports environment variables, config files, and CLI overrides.

Version: 1.0.1
Created: 2025-12-17
Updated: 2025-12-17 - Added future annotations for Python 3.10+ compatibility
"""
from __future__ import annotations

import os
import json
import logging
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class CacheConfig:
    """Local cache configuration."""
    enabled: bool = True
    max_entries: int = 1000
    ttl_seconds: int = 300  # 5 minutes
    search_cache_ttl: int = 60  # 1 minute for search results
    persist_to_disk: bool = False
    disk_path: Optional[str] = None


@dataclass
class ConnectionConfig:
    """Connection to DAKB gateway."""
    gateway_url: str = "http://localhost:3100"
    timeout_seconds: float = 30.0
    max_retries: int = 3
    keepalive_connections: int = 5


@dataclass
class ServerConfig:
    """Local proxy server configuration."""
    host: str = "127.0.0.1"
    port: int = 3110
    stdio_enabled: bool = True
    http_enabled: bool = True


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    file: Optional[str] = None


@dataclass
class ProxyConfig:
    """
    Complete proxy configuration.

    Configuration sources (in order of precedence):
    1. CLI arguments
    2. Environment variables (DAKB_PROXY_*)
    3. Config file (~/.dakb/proxy.json)
    4. Defaults
    """
    cache: CacheConfig = field(default_factory=CacheConfig)
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    auth_token: Optional[str] = None

    # Config file location
    DEFAULT_CONFIG_DIR = Path.home() / ".dakb"
    DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "proxy.json"

    @classmethod
    def load(cls, config_file: Optional[Path] = None) -> "ProxyConfig":
        """
        Load configuration from all sources.

        Args:
            config_file: Optional config file path override

        Returns:
            Merged configuration
        """
        config = cls()

        # Load from config file if exists
        file_path = config_file or cls.DEFAULT_CONFIG_FILE
        if file_path.exists():
            try:
                with open(file_path) as f:
                    file_config = json.load(f)
                config = cls._merge_dict(config, file_config)
                logger.info(f"Loaded config from {file_path}")
            except Exception as e:
                logger.warning(f"Failed to load config file: {e}")

        # Override from environment variables
        config = cls._load_from_env(config)

        return config

    @classmethod
    def _load_from_env(cls, config: "ProxyConfig") -> "ProxyConfig":
        """Load configuration from environment variables."""
        env_mappings = {
            "DAKB_PROXY_GATEWAY_URL": ("connection", "gateway_url"),
            "DAKB_PROXY_TIMEOUT": ("connection", "timeout_seconds", float),
            "DAKB_PROXY_HOST": ("server", "host"),
            "DAKB_PROXY_PORT": ("server", "port", int),
            "DAKB_PROXY_STDIO_ENABLED": ("server", "stdio_enabled", cls._parse_bool),
            "DAKB_PROXY_HTTP_ENABLED": ("server", "http_enabled", cls._parse_bool),
            "DAKB_PROXY_CACHE_ENABLED": ("cache", "enabled", cls._parse_bool),
            "DAKB_PROXY_CACHE_TTL": ("cache", "ttl_seconds", int),
            "DAKB_PROXY_LOG_LEVEL": ("logging", "level"),
            "DAKB_PROXY_LOG_FILE": ("logging", "file"),
            "DAKB_AUTH_TOKEN": ("auth_token",),
        }

        for env_var, path in env_mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                try:
                    # Handle type conversion
                    if len(path) == 3:
                        section, key, converter = path
                        value = converter(value)
                        setattr(getattr(config, section), key, value)
                    elif len(path) == 2:
                        section, key = path
                        setattr(getattr(config, section), key, value)
                    else:
                        setattr(config, path[0], value)
                except Exception as e:
                    logger.warning(f"Failed to parse {env_var}: {e}")

        return config

    @classmethod
    def _merge_dict(cls, config: "ProxyConfig", data: dict[str, Any]) -> "ProxyConfig":
        """Merge dictionary data into config."""
        if "cache" in data:
            for key, value in data["cache"].items():
                if hasattr(config.cache, key):
                    setattr(config.cache, key, value)

        if "connection" in data:
            for key, value in data["connection"].items():
                if hasattr(config.connection, key):
                    setattr(config.connection, key, value)

        if "server" in data:
            for key, value in data["server"].items():
                if hasattr(config.server, key):
                    setattr(config.server, key, value)

        if "logging" in data:
            for key, value in data["logging"].items():
                if hasattr(config.logging, key):
                    setattr(config.logging, key, value)

        if "auth_token" in data:
            config.auth_token = data["auth_token"]

        return config

    @staticmethod
    def _parse_bool(value: str) -> bool:
        """Parse boolean from string."""
        return value.lower() in ("true", "1", "yes", "on")

    def save(self, config_file: Optional[Path] = None) -> None:
        """
        Save configuration to file.

        Args:
            config_file: Optional config file path override
        """
        file_path = config_file or self.DEFAULT_CONFIG_FILE

        # Ensure directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "cache": asdict(self.cache),
            "connection": asdict(self.connection),
            "server": asdict(self.server),
            "logging": asdict(self.logging),
        }

        # Don't save auth token to file
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved config to {file_path}")

    def validate(self) -> list[str]:
        """
        Validate configuration.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        if not self.connection.gateway_url:
            errors.append("Gateway URL is required")

        if not self.auth_token:
            errors.append("Auth token is required (set DAKB_AUTH_TOKEN)")

        if self.server.port < 1 or self.server.port > 65535:
            errors.append(f"Invalid port: {self.server.port}")

        if self.connection.timeout_seconds <= 0:
            errors.append("Timeout must be positive")

        if self.cache.max_entries <= 0:
            errors.append("Cache max entries must be positive")

        return errors

    def setup_logging(self) -> None:
        """Configure logging based on config."""
        logging.basicConfig(
            level=getattr(logging, self.logging.level.upper()),
            format=self.logging.format,
        )

        if self.logging.file:
            file_handler = logging.FileHandler(self.logging.file)
            file_handler.setFormatter(logging.Formatter(self.logging.format))
            logging.getLogger().addHandler(file_handler)
