"""AdapterRegistry — dynamic adapter loading for the Chat Bridge.

Maintains a registry of :class:`ChatAdapter` instances keyed by platform name.
``auto_load()`` scans environment variables for bot tokens and dynamically
imports the corresponding adapter class.

The registry is **inert by default**: an adapter is only imported and
registered when its platform's bot-token environment variable is set. With no
tokens configured, ``auto_load()`` registers nothing and the chat bridge stays
dormant.
"""

import importlib
import inspect
import logging
import os

from dakb.chat_bridge.adapters.base import ChatAdapter

logger = logging.getLogger(__name__)

# Maps platform name -> (env_var, module_path, class_name)
# Only the Telegram adapter ships as a reference implementation; the other
# entries describe the expected module/class layout for additional adapters.
PLATFORM_CONFIG: dict[str, tuple[str, str, str]] = {
    "telegram": (
        "TELEGRAM_BOT_TOKEN",
        "dakb.chat_bridge.adapters.telegram_adapter",
        "TelegramAdapter",
    ),
    "discord": (
        "DISCORD_BOT_TOKEN",
        "dakb.chat_bridge.adapters.discord_adapter",
        "DiscordAdapter",
    ),
    "slack": (
        "SLACK_BOT_TOKEN",
        "dakb.chat_bridge.adapters.slack_adapter",
        "SlackAdapter",
    ),
    "whatsapp": (
        "WHATSAPP_BOT_TOKEN",
        "dakb.chat_bridge.adapters.whatsapp_adapter",
        "WhatsAppAdapter",
    ),
}

# Maps platform name -> env var holding the inbound webhook signing secret.
# Adapters whose __init__ does not accept a ``webhook_secret`` kwarg are left
# untouched (see _build_adapter).
SECRET_ENV_CONFIG: dict[str, str] = {
    "telegram": "TELEGRAM_SECRET_TOKEN",
    "discord": "DISCORD_WEBHOOK_SECRET",
    "slack": "SLACK_SIGNING_SECRET",
    "whatsapp": "WHATSAPP_WEBHOOK_SECRET",
}


def _build_adapter(cls, platform: str, token: str) -> ChatAdapter:
    """Instantiate *cls*, wiring in the per-platform webhook secret when the
    constructor accepts a ``webhook_secret`` keyword.

    Keeping the secret injection behind an ``inspect.signature`` gate means
    adapters that do not (yet) take a ``webhook_secret`` parameter are
    constructed exactly as before — preserving forward/backward compatibility.
    """
    kwargs = {"bot_token": token}
    try:
        accepts_secret = "webhook_secret" in inspect.signature(cls).parameters
    except (TypeError, ValueError):
        accepts_secret = False
    if accepts_secret:
        secret_env = SECRET_ENV_CONFIG.get(platform)
        secret = os.environ.get(secret_env) if secret_env else None
        # Pass the kwarg even when None so the adapter's own default/None-guard
        # (fail-closed) logic applies. Only attach it for adapters that accept it.
        kwargs["webhook_secret"] = secret
    return cls(**kwargs)


class AdapterRegistry:
    """Registry of chat platform adapters, keyed by platform name."""

    def __init__(self) -> None:
        self._adapters: dict[str, ChatAdapter] = {}

    def register(self, adapter: ChatAdapter) -> None:
        """Register an adapter, keyed by its platform property."""
        self._adapters[adapter.platform] = adapter

    def get(self, platform: str) -> ChatAdapter | None:
        """Return the adapter for *platform*, or None if not registered."""
        return self._adapters.get(platform)

    def has(self, platform: str) -> bool:
        """Return True if an adapter is registered for *platform*."""
        return platform in self._adapters

    def list_adapters(self) -> list[str]:
        """Return a list of registered platform name strings."""
        return list(self._adapters.keys())

    def get_all(self) -> dict[str, ChatAdapter]:
        """Return dict mapping platform names to adapter instances."""
        return dict(self._adapters)

    def auto_load(self) -> list[str]:
        """Scan env vars for bot tokens; import and register matching adapters.

        Returns:
            List of platform names that were successfully loaded. Empty when no
            platform token is configured (the inert-by-default case).
        """
        loaded: list[str] = []
        for platform, (env_var, module_path, class_name) in PLATFORM_CONFIG.items():
            token = os.environ.get(env_var)
            if not token:
                continue
            try:
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)
                adapter = _build_adapter(cls, platform, token)
                self.register(adapter)
                loaded.append(platform)
                logger.info("Auto-loaded %s adapter", platform)
            except Exception:
                logger.warning("Failed to load %s adapter", platform, exc_info=True)
        return loaded
