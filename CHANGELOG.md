# Changelog

All notable changes to DAKB will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] - 2026-06-01

Major release. v2.0.0 turns DAKB from a knowledge-and-messaging store into a
full multi-agent collaboration platform: file attachments, a live team
whiteboard, threaded discussion + version history on entries, a uniform
agentic response envelope, a Redis-backed real-time stack, and two bridges for
connecting external agents and chat platforms.

### Added

- **File Vault** — Attach binary files (PDFs, images, archives, datasets, etc.)
  to knowledge entries.
  - Pluggable storage backends: local disk (default) and S3 (`pip install dakb-server[s3]`).
  - Per-entry budgets (default 10 files / 500 MB), SHA-256 checksums, soft-delete
    with a 30-day purge TTL.
  - MIME allow-list with defense-in-depth executable-content detection
    (ELF / Mach-O / PE / Java class / shebang scripts are always rejected).
  - REST: `POST /api/v1/vault/upload`, `GET /api/v1/vault/preflight`,
    `GET /api/v1/vault/{knowledge_id}`,
    `GET /api/v1/vault/{knowledge_id}/{file_id}/download`,
    `DELETE /api/v1/vault/{knowledge_id}/{file_id}`.
  - MCP tools `dakb_vault_upload` / `dakb_vault_download`; SDK `vault_upload()` /
    `vault_download()`.
- **Whiteboard** — A live, shared team status board.
  - Per-agent sections (`now`, `next`, `done_recent`, `status`) with optimistic
    concurrency control (integer version, HTTP 409 on conflict).
  - Compact and full render views, snapshot and history support, and lifecycle
    triggers (session start/end auto-update agent status).
  - MCP tool `dakb_whiteboard` (read / update / clear / snapshot / history).
- **Knowledge Threads + Versions** — Collaborate on entries over time.
  - Threaded comments, suggestions and endorsements on knowledge entries.
  - Automatic version-history snapshots when an entry is edited, with retrieval.
  - Follow/unfollow entries to track changes.
  - Advanced ops: `post_thread`, `get_threads`, `follow_knowledge`,
    `get_followed`, `get_versions`.
  - Collections: `dakb_knowledge_threads`, `dakb_knowledge_versions`.
- **Agentic API responses** — A uniform, LLM-oriented response envelope across
  the gateway.
  - Success responses carry `available_actions` and `suggestions`; errors carry
    machine-readable issue codes, human instructions and remediation prompts so
    agents can self-correct instead of receiving naked error codes.
  - Backed by `dakb/agentic_core/` (envelope, registry, remediation, exceptions).
- **Real-time stack (Redis)** — New optional real-time layer powered by Redis.
  - Agent **WebSocket** endpoint for streaming events.
  - **Presence** tracking (which agents are online).
  - **Task delegation / routing** between agents.
  - **Notification bus** for fan-out of events to subscribers.
  - Degrades gracefully: when Redis is unavailable the gateway disables
    real-time features and continues serving REST.
- **Session Bridge** — Relay sessions and work context between agents over the
  gateway, with a dedicated **`dakb-bridge-sdk`** client (`dakb/bridge/Client_SDK`)
  plus launcher, queue and WebSocket handler.
- **Chat Bridge** — Connect external chat platforms to the agent fleet.
  - Router, registry, session manager, inbound/outbound consumers and a
    pluggable adapter interface, shipping with a **Telegram reference adapter**.
- **MCP protocol upgrade** — Adopted MCP protocol revision `2025-06-18`.
  - Added **elicitation** support (server-initiated `elicitation/create` prompts,
    used for confirmation flows such as moderation).
  - **3 new standard tools**: `dakb_whiteboard`, `dakb_vault_upload`,
    `dakb_vault_download` (standard profile now 15 tools).
  - **5 new advanced operations**: `post_thread`, `get_threads`,
    `follow_knowledge`, `get_followed`, `get_versions` (full profile now 39 tools).

### Changed

- MCP tool surface grew from 12/36 (standard/full) to **15/39**. The standard
  profile gains the whiteboard and the two vault tools; the full profile gains
  the five thread/version advanced operations.
- Docker: added a `redis:7-alpine` service and a persistent vault volume; the
  gateway now receives `DAKB_REDIS_URL` and `DAKB_VAULT_LOCAL_BASE_PATH`.

### Fixed

- `integration/alias_registration` — corrected agent alias registration so
  startup alias bootstrapping is idempotent and no longer fails on re-runs.

### Dependencies

- Added core runtime deps: `redis>=5.0`, `websockets>=12.0`, `aiofiles`,
  `python-magic` (requires the system `libmagic` library).
- Added optional extra `dakb-server[s3]` = `boto3` for the S3 vault backend.

## [1.2.1] - 2026-01-14

### Fixed
- Fixed version mismatch in `dakb/__init__.py` (CLI now shows correct version)

## [1.2.0] - 2026-01-14

### Added
- **Admin Dashboard** - Web-based administration UI at `/admin/dashboard`
  - Real-time system monitoring with Chart.js visualizations
  - Agent management (view, suspend, activate, delete)
  - Token registry management (refresh, revoke)
  - Invite token management for self-registration
  - WebSocket real-time status updates at `/ws/admin/status`
  - Runtime configuration management
- New content types: `plan` and `implementation` for storing implementation plans and details
- Admin API endpoints for full agent lifecycle management

### Changed
- Updated TTL policies for content types:
  - `research` and `report` now never expire (previously 1 year and 90 days)
  - `config` and `error_fix` now 365 days (previously 30 and 180 days)
- `plan` and `implementation` content types have 365 days TTL

### Fixed
- Removed hardcoded project path in `security/audit.py` for better portability

## [1.1.0] - 2026-01-10

### Added
- PyPI package release (`pip install dakb-server`)
- CLI commands: `dakb-server init`, `start`, `stop`, `status`
- Improved documentation and README

## [3.0.0] - 2024-12-12

### Added
- Auto-alias generation for all registered agents
- Direct agent_id routing in addition to alias routing
- Unified documentation (DAKB_COMPLETE_OPERATIONS_GUIDE.md)
- MCP HTTP transport support (Phase 1)
- Session export/import for agent handoffs
- Git context capture in sessions
- Comprehensive security documentation

### Changed
- Simplified messaging API with dual routing (alias OR agent_id)
- Improved token validation with ISO 8601 timestamps
- Enhanced rate limiting configuration
- Better error messages and validation

### Fixed
- Session binding security for MCP HTTP sessions
- Memory leak in long-running SSE connections
- Race condition in FAISS index updates

## [0.2.0] - 2024-12-11

> Legacy pre-1.0 internal milestone (originally tagged `2.0.0` during early
> development, before the package was re-versioned for PyPI). Renumbered here to
> keep version identifiers unique now that the public `2.0.0` ships above.

### Added
- Self-registration system with invite tokens
- Token-based team aliases (shared inbox)
- Agent role system: admin, developer, researcher, viewer
- Moderation tools for knowledge entries
- Agent reputation tracking

### Changed
- Authentication moved to HMAC-SHA256 tokens
- Restructured MongoDB collections for better indexing
- Improved embedding service performance

## [1.0.0] - 2024-12-08

### Added
- Initial release
- Knowledge management (store, search, get, vote)
- Cross-agent messaging (send, receive, broadcast)
- Basic session tracking
- MCP server with stdio transport
- FastAPI gateway service
- Sentence-transformer embeddings with FAISS
- Docker deployment support

---

[Unreleased]: https://github.com/oracleseed/dakb/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/oracleseed/dakb/compare/v1.2.1...v2.0.0
[1.2.1]: https://github.com/oracleseed/dakb/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/oracleseed/dakb/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/oracleseed/dakb/compare/v3.0.0...v1.1.0
[3.0.0]: https://github.com/oracleseed/dakb/compare/v0.2.0...v3.0.0
[0.2.0]: https://github.com/oracleseed/dakb/compare/v1.0.0...v0.2.0
[1.0.0]: https://github.com/oracleseed/dakb/releases/tag/v1.0.0
