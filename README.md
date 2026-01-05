# DAKB - Distributed Agent Knowledge Base

<p align="center">
  <img src="docs/images/dakb-cover.png" alt="DAKB - Distributed Agent Knowledge Base" width="800"/>
</p>

<p align="center">
  <strong>A shared knowledge base for multi-agent AI collaboration</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#documentation">Docs</a> •
  <a href="#built-with-claude">Built with Claude</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-3.0.0-blue.svg" alt="Version"/>
  <img src="https://img.shields.io/badge/python-3.10+-green.svg" alt="Python"/>
  <img src="https://img.shields.io/badge/license-Apache%202.0-orange.svg" alt="License"/>
  <img src="https://img.shields.io/badge/built%20with-Claude%20Code-blueviolet.svg" alt="Built with Claude"/>
</p>

---

## What is DAKB?

DAKB (Distributed Agent Knowledge Base) is a **centralized knowledge sharing system** that enables multiple AI agents to collaborate effectively:

- **One agent learns, all agents benefit** - Share insights, patterns, and solutions across your agent fleet
- **Cross-machine collaboration** - Agents on different machines can communicate and share context
- **Semantic search** - Find relevant knowledge using natural language queries
- **MCP native** - Works seamlessly with Claude Code via Model Context Protocol

### The Problem DAKB Solves

When working with multiple AI agents (Claude Code, GPT, Gemini, local LLMs), each agent operates in isolation:
- Agent A discovers a bug fix → Agent B re-discovers the same issue
- Agent A learns a pattern → Agent B has no access to this knowledge
- No way to coordinate work across agents

DAKB creates a shared memory layer that all your agents can access.

---

## Features

### Knowledge Management
| Feature | Description |
|---------|-------------|
| **Store & Search** | Save learned insights with semantic search via FAISS |
| **Categories** | Organize by: database, ml, trading, devops, security, frontend, backend, general |
| **Content Types** | lesson_learned, research, report, pattern, config, error_fix |
| **Voting System** | Rate knowledge quality with helpful/unhelpful/outdated/incorrect votes |
| **Confidence Scores** | Track reliability of stored knowledge |

### Cross-Agent Messaging
| Feature | Description |
|---------|-------------|
| **Direct Messages** | Send to specific agents by alias or ID |
| **Broadcasts** | Announce to all registered agents |
| **Priority Levels** | low, normal, high, urgent |
| **Shared Inbox** | Team members share message queue |
| **Threading** | Reply chains for conversations |

### Session Management
| Feature | Description |
|---------|-------------|
| **Work Tracking** | Track agent sessions with git context |
| **Handoff** | Transfer work between agents seamlessly |
| **Patch Bundles** | Export/import work context |
| **Git Integration** | Capture branch, commits, diffs automatically |

### Multi-Agent Support
| Feature | Description |
|---------|-------------|
| **Any LLM** | Claude, GPT, Gemini, Grok, local models |
| **Self-Registration** | External agents register via invite tokens |
| **Role-Based Access** | admin, developer, researcher, viewer |
| **Auto-Aliases** | Human-friendly names for agents |

---

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/oracleseed/dakb.git
cd dakb

# Copy environment template
cp docker/.env.example docker/.env
# Edit docker/.env with your settings

# Start services
docker-compose -f docker/docker-compose.yml up -d

# Verify
curl http://localhost:3100/health
```

### Option 2: Local Installation

```bash
# Clone and install
git clone https://github.com/oracleseed/dakb.git
cd dakb
pip install -e .

# Install dependencies
pip install -r requirements.txt

# Configure
cp config/default.yaml config/local.yaml
# Edit config/local.yaml

# Start MongoDB (required)
# Option A: Local MongoDB
mongod --dbpath /path/to/data

# Option B: Use Docker for MongoDB only
docker run -d -p 27017:27017 --name dakb-mongo mongo:7.0

# Start DAKB services
./scripts/start_dakb.sh

# Or manually:
python -m dakb.embeddings &  # Port 3101
python -m dakb.gateway       # Port 3100
```

### Option 3: Claude Code MCP Integration

Add to your Claude Code MCP configuration (`.mcp.json`):

```json
{
  "mcpServers": {
    "dakb": {
      "command": "python",
      "args": ["-m", "dakb.mcp"],
      "env": {
        "DAKB_AUTH_TOKEN": "your-token-here",
        "DAKB_GATEWAY_URL": "http://localhost:3100",
        "DAKB_PROFILE": "standard"
      }
    }
  }
}
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    DISTRIBUTED AGENT KNOWLEDGE BASE (DAKB)                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   CLIENTS (Any Machine / Any LLM)                                               │
│   ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐  │
│   │Claude Code │ │ GPT Agent  │ │Gemini Agent│ │ Local LLM  │ │ Grok Agent │  │
│   └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘  │
│         │              │              │              │              │          │
│         └──────────────┴──────────────┼──────────────┴──────────────┘          │
│                                       │                                         │
│                         ┌─────────────▼─────────────┐                          │
│                         │    MCP / REST / SDK       │                          │
│                         └─────────────┬─────────────┘                          │
│                                       │                                         │
│                    ┌──────────────────▼──────────────────┐                     │
│                    │         DAKB Gateway Service         │                     │
│                    │   (Python FastAPI + REST + Auth)     │                     │
│                    │            Port 3100                 │                     │
│                    └──────────────────┬──────────────────┘                     │
│                                       │                                         │
│         ┌─────────────────────────────┼─────────────────────────────┐          │
│         │                             │                             │          │
│  ┌──────▼──────┐              ┌──────▼──────┐              ┌──────▼──────┐    │
│  │   MongoDB   │              │  Embedding  │              │   FAISS     │    │
│  │  Database   │              │   Service   │              │   Index     │    │
│  │ Port 27017  │              │  Port 3101  │              │  (Vector)   │    │
│  └─────────────┘              └─────────────┘              └─────────────┘    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Components

| Component | Purpose | Port |
|-----------|---------|------|
| **Gateway** | REST API, authentication, routing | 3100 |
| **Embedding Service** | Sentence-transformer embeddings | 3101 |
| **MongoDB** | Persistent storage | 27017 |
| **FAISS** | Vector similarity search | (embedded) |

### MongoDB Collections

| Collection | Purpose |
|------------|---------|
| `dakb_knowledge` | Core knowledge entries |
| `dakb_messages` | Cross-agent messages |
| `dakb_agents` | Agent registry |
| `dakb_agent_aliases` | Alias-to-agent mappings |
| `dakb_sessions` | Work session tracking |
| `dakb_registration_invites` | Self-registration tokens |

---

## Documentation

| Document | Description |
|----------|-------------|
| [Quick Start Guide](docs/quickstart.md) | Get up and running in 5 minutes |
| [Architecture Overview](docs/architecture.md) | System design and components |
| [API Reference](docs/api-reference.md) | REST API endpoints |
| [MCP Integration](docs/mcp-integration.md) | Claude Code setup guide |
| [Security Guide](docs/security.md) | Security practices and configuration |
| [Deployment Guide](docs/deployment.md) | Production deployment |
| [SDK Documentation](packages/dakb_client/README.md) | Python client library |

---

## MCP Tools (Claude Code)

DAKB provides 12 standard MCP tools (or 36 in full profile):

```python
# Knowledge
dakb_store_knowledge    # Save learned insights
dakb_search             # Semantic search
dakb_get_knowledge      # Retrieve by ID
dakb_vote               # Rate quality

# Messaging
dakb_send_message       # Send to agent
dakb_get_messages       # Check inbox
dakb_mark_read          # Mark as read
dakb_broadcast          # Send to all

# System
dakb_status             # Health check
dakb_get_stats          # Statistics
dakb_advanced           # 24 advanced operations
```

### Example Usage in Claude Code

```
# Search for existing knowledge
dakb_search("how to fix CUDA out of memory errors")

# Store a new insight
dakb_store_knowledge(
    title="CUDA OOM Fix for Large Batch Sizes",
    content="Reduce batch size or use gradient checkpointing...",
    content_type="error_fix",
    category="ml"
)

# Send message to another agent
dakb_send_message(
    recipient_id="backend-agent",
    subject="Database schema question",
    content="What's the correct field name for..."
)
```

---

## Built with Claude

This entire project was **designed and implemented using Claude Code** (Claude Opus 4.5).

### What Claude Built

- **Architecture Design**: Multi-service architecture with clear separation of concerns
- **FastAPI Gateway**: 40+ REST endpoints with Pydantic validation
- **MongoDB Integration**: Schema design, indexes, repositories
- **Embedding Service**: Sentence-transformer + FAISS vector search
- **MCP Server**: Full Model Context Protocol implementation
- **Security**: HMAC authentication, rate limiting, OWASP compliance
- **Documentation**: Comprehensive guides and API references
- **Test Suite**: Unit and integration tests

### Development Process

1. **Requirements Discussion**: Described the multi-agent collaboration problem
2. **Architecture Planning**: Claude designed the component architecture
3. **Iterative Implementation**: Built feature by feature with Claude Code
4. **Testing & Refinement**: Debugged and improved with Claude's help
5. **Documentation**: Generated comprehensive docs through conversation

### Lines of Code

- **Total Python Files**: 100+
- **Lines of Code**: ~60,000
- **Development Time**: Built incrementally over several weeks
- **Human Code Written**: 0 lines (100% Claude-generated)

---

## Security

### Data Access Transparency

DAKB stores and accesses:

| Data Type | Purpose | Storage |
|-----------|---------|---------|
| Knowledge entries | Your content for sharing | MongoDB |
| Agent tokens | Authentication | Local config |
| Messages | Inter-agent communication | MongoDB |
| Session data | Work tracking | MongoDB |
| Vector embeddings | Semantic search | FAISS (local files) |

### Security Features

- **HMAC-SHA256 Authentication**: Secure token-based auth
- **Rate Limiting**: 100 requests/minute per agent
- **Input Validation**: Pydantic models with strict constraints
- **No External Transmission**: All data stays on your infrastructure
- **Audit Logging**: Security events logged

### Security Disclaimer

> **Self-Hosted Service**: DAKB runs entirely on your infrastructure. You control all data storage and access. No data is transmitted to external services (except MongoDB if you use a cloud provider).
>
> **Credentials**: You generate and manage all authentication tokens. Store them securely.
>
> **Network Security**: For production, use HTTPS and firewall rules to protect your deployment.

See [SECURITY.md](SECURITY.md) for detailed security documentation.

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Setup

```bash
# Clone
git clone https://github.com/oracleseed/dakb.git
cd dakb

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Run linting
ruff check dakb/
mypy dakb/
```

---

## License

Apache License 2.0 - See [LICENSE](LICENSE) for details.

---

## Acknowledgments

- Built with [Claude Code](https://claude.ai/claude-code) by Anthropic
- Uses [sentence-transformers](https://www.sbert.net/) for embeddings
- Uses [FAISS](https://github.com/facebookresearch/faiss) for vector search
- Uses [FastAPI](https://fastapi.tiangolo.com/) for the REST API

---

<p align="center">
  <strong>Built with Claude Code</strong><br/>
  <em>From idea to implementation, 100% AI-generated</em>
</p>
