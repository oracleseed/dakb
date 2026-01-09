# Claude Code Community Showcase Post

**Copy this post for sharing on the Claude Code Discord community**

---

## Cover Image

Post this image first:
```
https://raw.githubusercontent.com/oracleseed/dakb/main/docs/images/dakb-community-cover.png
```

---

## Discord Post Content (Context-Focused)

```
🧠 DAKB - Distributed Agent Knowledge Base

Hey everyone! I built something to solve a frustration I kept running into: context loss.

You know the drill — you're deep into a complex project with Claude Code, everything's flowing... then the conversation ends. Next session? You're re-explaining everything. Your agent forgot what took hours to establish.

DAKB is my attempt at solving this. It's a RAG-powered knowledge base that creates persistent, searchable memory for your AI agents.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 What It Actually Does

• 🔍 RAG Knowledge Base — Semantic search using FAISS + embeddings
• 💬 Cross-Agent Messaging — Agents share context across machines
• 📚 Persistent Memory — Insights survive beyond any single session
• ⚡ MCP Native — 36 tools for Claude Code integration

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

❓ The Real Problem: Context Is Your Bottleneck

It's not just about session time limits. The deeper problem:

❌ Agent A discovers a solution → Agent B re-discovers it from scratch
❌ You become the "human context relay" between sessions
❌ Multi-week projects lose accumulated insights
❌ Research findings scatter across conversations

✅ DAKB creates a shared knowledge layer — your agents accumulate and share context over time, not just within a single conversation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔄 Context Sharing & Tracking

This is the core value. DAKB sessions track and share context, not just time:

# Store context as you work (agent learns something)
dakb_store_knowledge(
    title="API rate limit pattern discovered",
    content="Kraken returns 429 after 15 req/sec...",
    tags=["api", "kraken", "rate-limit"]
)

# Any agent, any session, finds it later
dakb_search("kraken rate limit")
# → Instantly retrieves the solution

# Track work sessions with git context
dakb_advanced(operation="session_start", params={
    "description": "Implementing momentum strategy",
    "git_branch": "feature/momentum-v2"
})

# Come back days later — context preserved
dakb_advanced(operation="session_status")
# → Shows: branch, recent commits, where you left off

# Export context for agent handoff
dakb_advanced(operation="session_export")
# → Bundle your work context for another agent

The key: context accumulates across agents and sessions. Your agent fleet gets smarter over time.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📖 Full Guide

I wrote a detailed guide on managing context for large projects:
https://github.com/oracleseed/dakb/blob/main/docs/CONTEXT_MANAGEMENT_GUIDE.md

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔗 Links

📦 GitHub: https://github.com/oracleseed/dakb
🖼️ Architecture: https://raw.githubusercontent.com/oracleseed/dakb/main/docs/images/dakb-skills-architecture.png

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Built 100% with Claude Code (Opus 4.5) • Apache 2.0 License

Happy to answer questions or discuss the architecture! 💬
```

---

## Key Features

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

### Skills Architecture
| Feature | Description |
|---------|-------------|
| **Centralized Skills** | Store skills once, all agents access them |
| **Version Control** | Tag skills with version numbers |
| **Semantic Discovery** | Find skills via natural language search |
| **Quality Tracking** | Vote on skill helpfulness |
| **Access Control** | Public, restricted, or secret skills |

---

## Technical Details

### Tech Stack
- **Backend**: Python 3.10+ / FastAPI / MongoDB
- **RAG**: Sentence-Transformers + FAISS for semantic search
- **Protocol**: MCP (stdio + HTTP transport)
- **Deployment**: Docker-ready with compose stack

### Architecture
```
┌─────────────────────────────────────────────────────────────┐
│                    DAKB Knowledge Base                       │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Skills Collection (content_type: pattern)          │    │
│  │  ├── skill-code-review                              │    │
│  │  ├── skill-data-analysis                            │    │
│  │  ├── skill-trading-backtest                         │    │
│  │  └── skill-drl-training                             │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                                │
                    Semantic Search (FAISS)
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
     ┌─────────┐          ┌─────────┐          ┌─────────┐
     │ Claude  │          │  GPT    │          │ Gemini  │
     │  Code   │          │ Agent   │          │ Agent   │
     └─────────┘          └─────────┘          └─────────┘
```

### Skill Retrieval Pattern
```python
# 1. Search for relevant skill
results = dakb_search(query="skill code review")

# 2. Get full skill content
skill = dakb_get_knowledge(knowledge_id="kn_20260107_xxx")

# 3. Provide feedback
dakb_vote(knowledge_id="kn_20260107_xxx", vote="helpful")
```

---

## Quick Start

### Docker (Recommended)
```bash
git clone https://github.com/oracleseed/dakb.git
cd dakb
cp docker/.env.example docker/.env
docker-compose -f docker/docker-compose.yml up -d
```

### Claude Code MCP Integration
Add to `.mcp.json`:
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

---

## Images

| Image | URL |
|-------|-----|
| Cover | `https://raw.githubusercontent.com/oracleseed/dakb/main/docs/images/dakb-community-cover.png` |
| Skills Architecture | `https://raw.githubusercontent.com/oracleseed/dakb/main/docs/images/dakb-skills-architecture.png` |

---

## License

Apache 2.0 - Free & Open Source
