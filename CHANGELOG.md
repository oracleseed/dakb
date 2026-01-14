# Changelog

All notable changes to DAKB will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

## [2.0.0] - 2024-12-11

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

[Unreleased]: https://github.com/oracleseed/dakb/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/oracleseed/dakb/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/oracleseed/dakb/compare/v3.0.0...v1.1.0
[3.0.0]: https://github.com/oracleseed/dakb/compare/v2.0.0...v3.0.0
[2.0.0]: https://github.com/oracleseed/dakb/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/oracleseed/dakb/releases/tag/v1.0.0
