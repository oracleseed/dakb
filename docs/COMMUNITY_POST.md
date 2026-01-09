# Claude Code Community Showcase Post

**Copy this post for sharing on the Claude Code Discord community**

---

## Cover Image

Post this image first:
```
https://raw.githubusercontent.com/oracleseed/dakb/main/docs/images/dakb-community-cover.png
```

---

## Community Response: Large Project Context Management

**Use this when responding to questions about managing context on large codebases**

```
Re: Effectively managing context on large projects

Hey! I built something that might help with exactly this problem.

With a codebase that size (150K+ LOC, hundreds of classes), the usual techniques work but hit a wall — you're manually doing the context engineering every session, and Claude still loses everything when the conversation ends.

I created DAKB (Distributed Agent Knowledge Base) to solve this. Here's how it works for large projects:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🌐 Distributed Knowledge (Not Local Markdown)

The big difference from markdown files: DAKB is a shared knowledge layer accessible by any agent, anywhere.

• Multiple Claude Code instances on different machines
• GPT, Gemini, Grok, local LLMs — any agent can connect
• Team members share the same knowledge base
• Store once, every agent accesses it

Your markdown file lives on one machine for one agent. DAKB lives on a server — your whole agent fleet shares it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 Semantic Search (Not Keyword Matching)

Store your architecture knowledge:

dakb_store_knowledge(
    title="Auth Service Architecture",
    content="900 classes breakdown: AuthController handles...",
    category="backend",
    tags=["architecture", "auth-service"]
)

Any agent, any machine, any LLM finds it:

dakb_search("what handles user authentication")
# → Returns your stored context via semantic similarity

No more grepping. No more re-explaining to each new session.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📂 For Large Method Count Problems

Build up knowledge across agents and sessions:

• Claude Code discovers a pattern → stores it
• GPT agent on another machine finds it later
• Team member's Gemini agent uses the same knowledge

Knowledge accumulates across your entire agent ecosystem.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔄 Session Continuity & Handoff

When context fills or you switch agents:

# Agent A (Claude) exports work state
dakb_advanced(operation="session_export")

# Agent B (GPT on different machine) picks up
dakb_advanced(operation="session_import", params={"session_id": "..."})

Cross-platform, cross-machine context transfer.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The core idea: your project knowledge lives in a shared, searchable layer — not locked in one markdown file on one machine. Any agent, anywhere, can access and contribute.

📦 GitHub: https://github.com/oracleseed/dakb
📖 Context Guide: https://github.com/oracleseed/dakb/blob/main/docs/CONTEXT_MANAGEMENT_GUIDE.md

Open source (Apache 2.0), built with Claude Code. Happy to answer questions!
```

---

## Discord Showcase Post (General Introduction)

**Use this for general showcase/announcement posts**

```
🧠 DAKB - Distributed Agent Knowledge Base

Hey everyone! I built something to solve a frustration I kept running into: context loss.

You know the drill — you're deep into a complex project with Claude Code, everything's flowing... then the conversation ends. Next session? You're re-explaining everything. Your agent forgot what took hours to establish.

DAKB is my attempt at solving this. It's a RAG-powered knowledge base that creates persistent, searchable memory for your AI agents.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🌐 What Makes It Different

Unlike local markdown files, DAKB is distributed:

• Multiple Claude Code instances on different machines share it
• GPT, Gemini, Grok, local LLMs — any agent can connect
• Team members share the same knowledge base
• Store once, every agent accesses it

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 What It Actually Does

• 🔍 RAG Knowledge Base — Semantic search using FAISS + embeddings
• 💬 Cross-Agent Messaging — Agents share context across machines
• 📚 Persistent Memory — Insights survive beyond any single session
• ⚡ MCP Native — 36 tools for Claude Code integration

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔄 Context Sharing Example

# Store context as you work
dakb_store_knowledge(
    title="API rate limit pattern discovered",
    content="Kraken returns 429 after 15 req/sec...",
    tags=["api", "kraken", "rate-limit"]
)

# Any agent, any machine, any session finds it later
dakb_search("kraken rate limit")
# → Instantly retrieves the solution

# Export for agent handoff
dakb_advanced(operation="session_export")
# → Bundle work context for another agent on different machine

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
