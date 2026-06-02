"""DAKB Chat Bridge — pluggable adapter architecture for external chat platforms.

Bridges external messaging platforms (Telegram, Discord, Slack, WhatsApp, ...)
into the DAKB real-time stack. Inbound webhooks are normalized to a unified
JSON-RPC 2.0 ``chat.inbound`` schema and published to Redis; outbound agent
replies are delivered back to the originating platform.

Components:
- adapters/: per-platform adapters (subclass ``ChatAdapter``)
- registry: env-driven adapter discovery (inert until a platform token is set)
- router: FastAPI ``/webhook/{platform}`` endpoint
- session_manager: MongoDB CRUD for chat sessions
- outbound_consumer: Redis stream consumer that delivers agent replies
- schemas: self-contained Pydantic models for chat sessions / messages / alerts
"""
