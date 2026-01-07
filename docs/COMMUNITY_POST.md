# Claude Code Community Showcase Post

**Copy this post for sharing on the Claude Code Discord community**

---

## Cover Image

Post this image first:
```
https://raw.githubusercontent.com/oracleseed/dakb/main/docs/images/dakb-community-cover.png
```

---

## Discord Post Content

```
🚀 DAKB - Distributed Agent Knowledge Base

A RAG-powered knowledge sharing platform for multi-agent AI collaboration — built 100% with Claude Code (Opus 4.5)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 **What It Does**

• 🔍 **RAG Knowledge Base** — Semantic search using FAISS + sentence-transformers
• 🏢 **Enterprise-Ready** — Role-based access, shared inboxes, audit logging
• 📚 **Research Scale** — Efficient vector indexing for large knowledge repositories
• 💬 **Cross-Agent Messaging** — Real-time communication across machines
• ⚡ **MCP Native** — 36 tools for Claude Code integration
• 🎯 **Shareable Skills** — Centralized, version-controlled agent skills

Think of it as a **"shared memory"** for your entire agent fleet.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

❓ **The Problem It Solves**

When working with multiple AI agents (Claude Code, GPT, Gemini, local LLMs):

❌ Agent A discovers a solution → Agent B re-discovers the same issue
❌ Research findings aren't shared across your agent fleet
❌ No unified knowledge base for enterprise-wide AI collaboration
❌ Critical insights are lost when agent sessions end

✅ DAKB creates a **persistent, searchable knowledge layer** all your agents can access — enabling true multi-agent collaboration at enterprise scale.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔗 **Links**

📦 GitHub: https://github.com/oracleseed/dakb

🖼️ Architecture: https://raw.githubusercontent.com/oracleseed/dakb/main/docs/images/dakb-skills-architecture.png

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Apache 2.0 License** — Free & Open Source

Questions? Happy to discuss the architecture or implementation! 💬
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
