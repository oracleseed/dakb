# DAKB (Distributed Agent Knowledge Base) Context for Gemini

## Project Overview

**DAKB** is a centralized knowledge sharing system designed to enable collaboration between multiple AI agents (Claude, GPT, Gemini, local LLMs). It solves the "isolated agent" problem by providing a shared memory layer where agents can store insights, search for solutions, and communicate with each other.

*   **Version:** 3.0.0
*   **Core Tech:** Python 3.10+, FastAPI, MongoDB, FAISS, Sentence Transformers, Docker.
*   **Protocol:** Native MCP (Model Context Protocol) support for integration with Claude Code and other MCP-compliant clients.

## Architecture

The system follows a microservices-like architecture, often orchestrated via Docker Compose.

```
[Clients (Agents)] -> [DAKB Gateway (FastAPI)] -> [MongoDB (Data)]
                                          |
                                          +-> [Embedding Service (FAISS/Sentence-Transformers)]
```

### Key Components

*   **Gateway Service (`dakb.gateway`):**
    *   **Port:** 3100
    *   **Role:** The main entry point. Handles REST API requests, authentication (JWT), routing, and logic for knowledge/messaging.
    *   **Endpoints:** `/api/v1/knowledge`, `/api/v1/messages`, `/api/v1/register`, `/health`.
*   **Embedding Service (`dakb.embeddings`):**
    *   **Port:** 3101
    *   **Role:** Computes vector embeddings for semantic search using `sentence-transformers` and manages the FAISS vector index.
*   **MongoDB:**
    *   **Port:** 27017
    *   **Role:** Persistent storage for knowledge entries, messages, agent registry, sessions, and audit logs.
*   **MCP Server (`dakb.mcp`):**
    *   **Role:** Exposes DAKB functionality as MCP tools over stdio, allowing direct integration with agents like Claude Code.

## Development Workflow

### 1. Environment Setup

**Prerequisites:** Python 3.10+, MongoDB, Git.

**Local Setup:**
```bash
# Create virtual env
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e ".[dev]"
```

**Docker Setup:**
```bash
cp docker/.env.example docker/.env
docker-compose -f docker/docker-compose.yml up -d
```

### 2. Key Commands

| Action | Command | Description |
| :--- | :--- | :--- |
| **Run Gateway** | `python -m dakb.gateway` | Starts the REST API on port 3100. |
| **Run Embeddings** | `python -m dakb.embeddings` | Starts the vector service on port 3101. |
| **Run Tests** | `pytest tests/` | Runs the full test suite. |
| **Linting** | `ruff check dakb/` | Lints the codebase. |
| **Type Check** | `mypy dakb/` | Static type checking. |
| **Formatting** | `ruff format dakb/` | Formats code to standards. |
| **Start All (Script)** | `./scripts/start_dakb.sh` | Helper script to start services. |

### 3. Coding Conventions

*   **Style:** PEP 8 with 100 char line limit.
*   **Docstrings:** Google-style docstrings required for all public entities.
*   **Type Hints:** Strictly enforced (`mypy`).
*   **Testing:** New features must have unit tests. Integration tests for API endpoints.

## Project Structure

*   `dakb/` - Main package source.
    *   `gateway/` - FastAPI application (routes, middleware).
    *   `embeddings/` - Vector search logic.
    *   `db/` - Database schemas (`Pydantic`) and repositories.
    *   `mcp/` - MCP server implementation.
    *   `messaging/`, `sessions/`, `security/` - Feature modules.
*   `docker/` - Dockerfiles and Compose configs.
*   `tests/` - Unit and integration tests.
*   `docs/` - Documentation.

## Key Data Models (`dakb/db/schemas.py`)

*   **DakbKnowledge:** Stores insights (`lesson_learned`, `error_fix`, `pattern`, etc.). Fields: `content`, `embedding_indexed`, `votes`.
*   **DakbMessage:** Inter-agent messaging. Supports direct messages and broadcasting.
*   **DakbAgent:** Registry of known agents, including capabilities and roles (`admin`, `developer`, `researcher`).
*   **DakbSession:** Tracks agent work sessions, including git context for handoffs.

## MCP Integration

The MCP server exposes DAKB tools to agents.

*   **Profile:** Configurable via `DAKB_PROFILE` env var (`standard` vs `full`).
*   **Tools:**
    *   `dakb_store_knowledge`, `dakb_search` (Core)
    *   `dakb_send_message`, `dakb_broadcast` (Messaging)
    *   `dakb_status` (System)
*   **Running MCP:** `python -m dakb.mcp` (communicates via stdio).

## Common Tasks for Gemini

1.  **Exploring Code:** Start with `dakb/gateway/main.py` for API logic or `dakb/db/schemas.py` for data structure.
2.  **Adding Features:** Follow the flow: Schema -> Repository -> Service/Route -> Test.
3.  **Debugging:** Check `logs/` (if configured) or standard output. Use `dakb/mcp/server.py` to debug MCP issues.
