# Claude Code Community Showcase Post

**Copy this post for sharing on the Claude Code Discord community**

**Cover Image**: Attach `docs/images/dakb-community-cover.png` or use this URL:
```
https://raw.githubusercontent.com/oracleseed/dakb/main/docs/images/dakb-community-cover.png
```

---

## DAKB - Distributed Agent Knowledge Base

**Built 100% with Claude Code (Claude Opus 4.5)**

### What I Built

DAKB is a **RAG-powered knowledge sharing platform** designed for **enterprise teamwork** and **large-scale research projects** through a multi-agent ecosystem:

- **RAG Knowledge Base**: High-quality semantic search using FAISS + sentence-transformers
- **Enterprise Collaboration**: Role-based access, shared inboxes, and team coordination
- **Cross-Agent Messaging**: Real-time communication between agents on different machines
- **Session Handoff**: Transfer work context between agents seamlessly
- **MCP Native**: 36 tools for Claude Code integration

Think of it as a **persistent, searchable knowledge layer** for your entire AI agent fleet - enabling true multi-agent collaboration at enterprise scale.

### Use Cases

| Scenario | How DAKB Helps |
|----------|----------------|
| **Enterprise Development** | Multiple Claude Code instances share bug fixes and patterns across teams |
| **Research Projects** | Accumulate and search research findings, papers, experimental results |
| **Multi-Agent Workflows** | Coordinate specialized agents (coder, reviewer, researcher) |
| **Knowledge Management** | Build institutional AI memory that persists across sessions |

### The Problem It Solves

When working with multiple AI agents in enterprise or research settings, each operates in isolation:
- Agent A discovers a solution → Agent B re-discovers the same issue
- Research findings aren't shared across the team's agent fleet
- No unified knowledge base for enterprise-wide AI collaboration
- Critical insights are lost when agent sessions end

DAKB creates a **persistent RAG knowledge layer** all your agents can access.

### How Claude Built This

This entire project - architecture to implementation - was built through conversations with Claude Code:

- **Architecture**: Multi-service design (Gateway + Embedding Service + MongoDB)
- **Implementation**: ~40,000 lines of Python, 100% Claude-generated
- **RAG Pipeline**: FAISS vector indexing with sentence-transformer embeddings
- **Security**: HMAC authentication, rate limiting, OWASP compliance
- **Documentation**: Comprehensive guides, API references, examples

The development process was iterative: describe what I needed, Claude Code implemented it, we debugged together, refined, and repeated.

### Tech Stack

- **Backend**: Python 3.10+ / FastAPI / MongoDB
- **RAG**: Sentence-Transformers + FAISS for semantic search
- **Protocol**: MCP (stdio + HTTP transport)
- **Deployment**: Docker-ready with compose stack

### Features

| Category | What You Get |
|----------|--------------|
| **RAG Knowledge** | Store, search semantically, vote on quality |
| **Messaging** | Direct messages, broadcasts, priority levels, threading |
| **Sessions** | Track work, git context capture, handoff between agents |
| **Enterprise** | Role-based access, audit logging, team inboxes |

### Security & Data Transparency

**Self-hosted**: DAKB runs entirely on your infrastructure. You control all data.

**What it stores**:
- Knowledge entries (your content)
- Agent auth tokens (you generate)
- Messages between agents
- Session tracking data
- Vector embeddings (local FAISS files)

**What it doesn't do**:
- No external data transmission
- No telemetry or analytics
- No cloud dependencies

### Try It

**GitHub**: https://github.com/oracleseed/dakb

**Quick Start**:
```bash
git clone https://github.com/oracleseed/dakb.git
cd dakb
cp docker/.env.example docker/.env
docker-compose -f docker/docker-compose.yml up -d
```

**Claude Code Integration** (add to `.mcp.json`):
```json
{
  "mcpServers": {
    "dakb": {
      "command": "python",
      "args": ["-m", "dakb.mcp"],
      "env": {
        "DAKB_AUTH_TOKEN": "your-token",
        "DAKB_GATEWAY_URL": "http://localhost:3100"
      }
    }
  }
}
```

### Free & Open Source

Apache 2.0 License - use it, modify it, build on it.

---

**Questions?** Happy to discuss the architecture, RAG implementation, enterprise use cases, or how Claude Code made this possible.
