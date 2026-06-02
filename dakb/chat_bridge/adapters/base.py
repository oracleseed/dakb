"""Abstract base class for chat platform adapters.

Every concrete adapter (Telegram, Slack, Discord, etc.) must subclass
``ChatAdapter`` and implement all abstract methods/properties.
"""

from abc import ABC, abstractmethod


class ChatAdapter(ABC):
    """Base class for pluggable chat platform adapters.

    Subclasses normalize platform-specific webhooks into JSON-RPC 2.0
    notifications with method='chat.inbound'.
    """

    @property
    @abstractmethod
    def platform(self) -> str:
        """Return platform identifier (e.g., 'telegram')."""
        ...

    @abstractmethod
    async def send_message(self, chat_id: str, content: str, **kwargs) -> dict:
        """Send a message to a chat. Returns dict with delivery status."""
        ...

    @abstractmethod
    async def handle_webhook(self, payload: dict, headers: dict) -> dict:
        """Parse inbound webhook into JSON-RPC 2.0 notification (method='chat.inbound')."""
        ...

    @abstractmethod
    def get_webhook_path(self) -> str:
        """Return URL path for this platform's webhook (e.g., '/webhook/telegram')."""
        ...

    @abstractmethod
    def validate_signature(self, headers: dict, body: bytes) -> bool:
        """Validate webhook request authenticity."""
        ...
