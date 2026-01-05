# Claude Code Community Showcase Post

**Copy this post for sharing on the Claude Code Discord community**

---

## DAKB - Distributed Agent Knowledge Base

**Built 100% with Claude Code (Claude Opus 4.5)**

### What I Built

DAKB is a shared knowledge base that enables multiple AI agents to collaborate effectively:

- **Knowledge Sharing**: Store insights, patterns, and error fixes that all agents can search semantically
- **Cross-Agent Messaging**: Send messages between agents running on different machines
- **Session Handoff**: Transfer work context between agents seamlessly
- **MCP Native**: Works with Claude Code via 36 MCP tools

Think of it as a "shared memory" for your agent fleet - one agent learns something, all agents benefit.

### The Problem It Solves

When working with multiple AI agents (Claude Code on different projects, GPT for certain tasks, local LLMs), each operates in isolation:
- Agent A discovers a fix → Agent B re-discovers the same issue
- No way to share learned patterns across your agent setup
- Can't coordinate work between agents on different machines

DAKB creates a persistent knowledge layer all your agents can access.

### How Claude Helped

This entire project - from architecture to implementation - was built through conversations with Claude Code:

- **Architecture**: Designed the multi-service architecture (Gateway + Embedding + MongoDB)
- **Implementation**: ~60,000 lines of Python code, 100% Claude-generated
- **Security**: HMAC authentication, rate limiting, OWASP compliance
- **Documentation**: Comprehensive guides, API references, examples

The development process was iterative: describe what I needed, Claude Code implemented it, we debugged together, refined, and repeated. No manual coding required.

### Tech Stack

- Python 3.10+ / FastAPI / MongoDB
- Sentence-Transformers + FAISS for semantic search
- MCP Protocol (stdio + HTTP transport)
- Docker-ready deployment

### Features

| Category | What You Get |
|----------|--------------|
| **Knowledge** | Store, search, vote on shared insights |
| **Messaging** | Direct messages, broadcasts, priority levels |
| **Sessions** | Track work, handoff between agents |
| **Security** | Token auth, rate limiting, audit logging |

### Security & Data Transparency

**Self-hosted**: DAKB runs entirely on your infrastructure. You control all data.

**What it stores**:
- Knowledge entries (your content)
- Agent auth tokens (you generate)
- Messages between agents
- Session tracking data

**What it doesn't do**:
- No external data transmission
- No telemetry or analytics
- No cloud dependencies

### Try It

**GitHub**: https://github.com/oracleseed/dakb

**Quick Start**:
```bash
# Docker (recommended)
git clone https://github.com/oracleseed/dakb.git
cd dakb
cp docker/.env.example docker/.env
# Edit docker/.env with your settings
docker-compose -f docker/docker-compose.yml up -d
```

**Claude Code Integration**:
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

**Questions?** Happy to discuss the architecture, implementation details, or how Claude Code made this possible.
