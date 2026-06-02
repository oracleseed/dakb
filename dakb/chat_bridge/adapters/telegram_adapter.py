"""Telegram adapter for the DAKB Chat Bridge.

Reference implementation of :class:`ChatAdapter`. Normalizes Telegram Bot API
webhooks into JSON-RPC 2.0 ``chat.inbound`` notifications and sends outbound
messages via the Bot API.

The bot token is supplied at construction time — it is never hardcoded. The
registry sources it from the ``TELEGRAM_BOT_TOKEN`` environment variable, so
the adapter is only ever instantiated when an operator has explicitly
configured a token.
"""

import hmac

import httpx

from dakb.chat_bridge.adapters.base import ChatAdapter


class TelegramAdapter(ChatAdapter):
    """Adapter for the Telegram Bot API."""

    def __init__(self, bot_token: str, webhook_secret: str | None = None):
        self._bot_token = bot_token
        self._webhook_secret = webhook_secret
        self._api_base = f"https://api.telegram.org/bot{bot_token}"

    @property
    def platform(self) -> str:
        return "telegram"

    def get_webhook_path(self) -> str:
        return "/webhook/telegram"

    def validate_signature(self, headers: dict, body: bytes) -> bool:
        # Fail CLOSED: if no secret is configured we cannot verify the request,
        # so we reject it. (Returning True here would accept any unsigned /
        # forged webhook — a fail-open security hole.)
        if not self._webhook_secret:
            return False
        # Case-insensitive header lookup
        normalized = {k.lower(): v for k, v in headers.items()}
        token = normalized.get("x-telegram-bot-api-secret-token")
        if token is None:
            return False
        # Constant-time comparison to avoid leaking the secret via timing.
        try:
            return hmac.compare_digest(str(token), str(self._webhook_secret))
        except (TypeError, ValueError):
            return False

    async def handle_webhook(self, payload: dict, headers: dict) -> dict | None:
        message = payload.get("message")
        if message is None:
            return None

        text = message.get("text")
        if text is None:
            return None

        from_user = message.get("from", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id", "")
        user_id = str(from_user.get("id", ""))

        params = {
            "source_platform": self.platform,
            "external_user_id": user_id,
            "external_chat_id": str(chat_id),
            "composite_chat_id": f"telegram:{chat_id}",
            "content": text,
            "content_type": "text",
        }

        # Detect /invite and /uninvite commands
        if text.startswith("/invite ") or text.startswith("/uninvite "):
            parts = text.split(maxsplit=1)
            command = parts[0].lstrip("/")
            command_args = parts[1] if len(parts) > 1 else ""
            params["is_command"] = True
            params["command"] = command
            params["command_args"] = command_args

        return {
            "jsonrpc": "2.0",
            "method": "chat.inbound",
            "params": params,
        }

    async def send_message(self, chat_id: str, content: str, **kwargs) -> dict:
        params = {"chat_id": chat_id, "text": content}
        if "parse_mode" in kwargs:
            params["parse_mode"] = kwargs["parse_mode"]
        return await self._send_api_request("sendMessage", params)

    async def _send_api_request(self, method: str, params: dict) -> dict:
        url = f"{self._api_base}/{method}"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=params, timeout=10.0)
                return response.json()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
