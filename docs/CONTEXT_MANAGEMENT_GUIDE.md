# Managing Context in Large Projects with DAKB

## The Context Problem

When working with AI agents on large projects (100K+ LOC, hundreds of classes) or deep research tasks, you'll hit a wall: **isolated context**.

### What Happens Without DAKB

```
Day 1: "Claude, let's build a trading bot..."
       → Claude learns your architecture, patterns, decisions

Day 2: "Let's continue..."
       → New session. Claude forgot everything.
       → You spend 30 min re-explaining the project.

Day 5: Deep in ML model optimization...
       → Context window full. Earlier insights lost.
       → "What was that fix we found for the GPU memory issue?"

Week 3: Multiple agents working on different parts...
       → Agent A (Claude on Machine 1) finds a bug pattern.
       → Agent B (GPT on Machine 2) encounters same bug. Re-discovers it.
       → No knowledge sharing between sessions, agents, or machines.
```

**The result**: You become the "human context relay" — constantly re-explaining, manually transferring knowledge between agents, losing insights when conversations end.

### The Markdown File Limitation

Many developers try markdown files for context:
```
project/
├── ARCHITECTURE.md
├── DECISIONS.md
└── CONTEXT.md
```

**The problem**: These files are:
- **Local to one machine** — your laptop, not your colleague's
- **Single agent** — Claude can read it, but GPT on another machine can't
- **Keyword-based** — you must know what file to reference
- **Not searchable** — can't find by meaning, only by exact terms

---

## How DAKB Solves This

DAKB creates a **distributed, searchable knowledge layer** accessible by any agent, anywhere:

```
┌─────────────────────────────────────────────────────────────┐
│                    DAKB Knowledge Base                       │
│                       (Server-Based)                         │
│                                                              │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────┐   │
│  │ Project        │ │ Research       │ │ Error Fixes    │   │
│  │ Architecture   │ │ Findings       │ │ & Patterns     │   │
│  └────────────────┘ └────────────────┘ └────────────────┘   │
│                                                              │
│  Persists • Searchable • Distributed • Multi-Agent          │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
    ┌────▼────┐          ┌────▼────┐          ┌────▼────┐
    │ Claude  │          │  GPT    │          │ Gemini  │
    │Machine 1│          │Machine 2│          │Machine 3│
    └─────────┘          └─────────┘          └─────────┘
```

### Key Difference: Distributed, Not Local

| Local Markdown | DAKB |
|----------------|------|
| One machine | Any machine on network |
| One agent | Claude, GPT, Gemini, Grok, local LLMs |
| Keyword search | Semantic similarity search |
| Manual reference | Automatic discovery |
| Single user | Team-wide sharing |

---

## Use Case 1: Large Codebase Development

### The Challenge
- 50,000+ lines of code
- Complex architecture with multiple services
- Decisions made months ago affect today's work
- Multiple developers/agents touching different parts

### DAKB Solution

**Store architectural decisions:**
```python
dakb_store_knowledge(
    title="Trading Bot Architecture Decisions",
    content="""
    ## Core Architecture (2026-01)

    ### Why FastAPI over Django REST
    - Need async for real-time data streams
    - WebSocket support critical for order book
    - Lighter weight for ML inference endpoints

    ### Database Choice: MongoDB
    - Time-series data fits document model
    - Flexible schema for evolving indicators
    - Aggregation pipeline for analytics

    ### Service Boundaries
    - data_fetch: External API integration
    - prepare_data: Feature engineering
    - DRL_T: Model training (isolated for GPU)
    - live_trade: Order execution
    """,
    content_type="research",
    category="backend",
    tags=["architecture", "decisions", "trading-bot"]
)
```

**Retrieve context in new sessions:**
```python
# New session, need to understand the architecture
results = dakb_search("trading bot architecture decisions")
# → Instantly recalls all architectural context
```

**Store bug patterns:**
```python
dakb_store_knowledge(
    title="CUDA OOM Fix - Large Batch DRL Training",
    content="""
    ## Problem
    CUDA out of memory during PPO training with batch_size > 256

    ## Root Cause
    Gradient accumulation not clearing between episodes

    ## Solution
    ```python
    optimizer.zero_grad()  # Add before each episode
    torch.cuda.empty_cache()  # Clear after validation
    ```

    ## Prevention
    Always use gradient checkpointing for models > 100M params
    """,
    content_type="error_fix",
    category="ml",
    tags=["cuda", "oom", "drl", "ppo", "training"]
)
```

---

## Use Case 2: Deep Research Tasks

### The Challenge
- Multi-week research project
- Reading dozens of papers
- Experimental results accumulating
- Need to connect findings across sessions

### DAKB Solution

**Store paper summaries:**
```python
dakb_store_knowledge(
    title="Paper: Attention Is All You Need - Key Insights",
    content="""
    ## Citation
    Vaswani et al., 2017, NeurIPS

    ## Key Contributions
    1. Self-attention replaces recurrence entirely
    2. Multi-head attention for multiple representation subspaces
    3. Positional encoding for sequence order

    ## Relevance to Our Project
    - Can apply transformer architecture to time-series trading data
    - Multi-head attention might capture different market regimes

    ## Follow-up Questions
    - How to handle variable-length sequences in live trading?
    - Computational cost for real-time inference?
    """,
    content_type="research",
    category="ml",
    tags=["paper", "transformer", "attention", "deep-learning"]
)
```

**Store experimental results:**
```python
dakb_store_knowledge(
    title="Experiment: TCN vs LSTM for Price Prediction",
    content="""
    ## Hypothesis
    TCN will outperform LSTM on 1-hour BTC/USD prediction

    ## Setup
    - Data: 2 years BTC/USD hourly candles
    - Features: OHLCV + 20 technical indicators
    - Train/Val/Test: 70/15/15

    ## Results
    | Model | MSE    | Sharpe | Max DD |
    |-------|--------|--------|--------|
    | LSTM  | 0.0023 | 1.2    | -15%   |
    | TCN   | 0.0019 | 1.5    | -12%   |

    ## Conclusion
    TCN 17% better MSE, significantly better risk-adjusted returns

    ## Next Steps
    - Try TCN with attention mechanism
    - Test on different timeframes
    """,
    content_type="research",
    category="ml",
    tags=["experiment", "tcn", "lstm", "price-prediction", "results"]
)
```

**Connect findings across sessions:**
```python
# Week 3: "What did we learn about TCN?"
results = dakb_search("TCN experiment results")

# Week 5: "Any papers on attention for time series?"
results = dakb_search("attention time series trading")
```

---

## Use Case 3: Multi-Agent, Multi-Platform Collaboration

### The Challenge
- Different agents handle different tasks (Backend, ML, Research)
- **Different LLMs**: Claude Code, GPT, Gemini, Grok, local models
- **Different machines**: Developer laptops, cloud servers, CI/CD agents
- Need to share findings without human relay

### DAKB Solution: Cross-Platform Knowledge Sharing

**Agent A (Claude on Machine 1) discovers a pattern:**
```python
# Backend Agent (Claude Code) finds an API issue
dakb_store_knowledge(
    title="Kraken API Rate Limit Pattern",
    content="""
    ## Discovery
    Kraken API returns 429 after 15 requests/second

    ## Workaround
    Implement exponential backoff:
    ```python
    for attempt in range(5):
        try:
            response = kraken_api.call()
            break
        except RateLimitError:
            time.sleep(2 ** attempt)
    ```
    """,
    content_type="pattern",
    category="backend",
    tags=["kraken", "api", "rate-limit", "pattern"]
)
```

**Agent B (GPT on Machine 2) finds it automatically:**
```python
# ML Agent (GPT) on different machine, days later
results = dakb_search("kraken api rate limit")
# → Finds the pattern without human intervention
# → No manual copy-paste between machines
```

**Agent C (Gemini on Machine 3) uses the same knowledge:**
```python
# Research Agent (Gemini) building documentation
results = dakb_search("API patterns and workarounds")
# → Same knowledge base, different LLM, different machine
```

### Cross-Agent Messaging

Direct communication between agents, regardless of LLM or location:
```python
# Claude agent alerts GPT agent
dakb_send_message(
    recipient_id="ml-agent",  # GPT on another machine
    subject="Kraken API Changes",
    content="API response format changed. Update your data parser.",
    priority="high"
)
```

---

## Use Case 4: Session Handoff Across Agents

### The Challenge
- Context window fills up mid-task
- Need to switch from Claude to GPT (or vice versa)
- Want to hand off work to a colleague's agent
- Continue tomorrow without re-explaining

### DAKB Solution: Session Export/Import

**Export your work state before ending:**
```python
# Claude agent on your laptop, context getting full
dakb_advanced(operation="session_export")
# → Captures: git branch, recent commits, what you were working on
# → Returns session_id for continuation
```

**Import on another agent/machine:**
```python
# GPT agent on colleague's machine, next day
dakb_advanced(operation="session_import", params={
    "session_id": "sess_20260109_143022_a1b2c3d4"
})
# → Loads: branch context, work state, where you left off
# → Agent picks up exactly where previous agent stopped
```

### Session Workflow
```
┌─────────────┐     Export      ┌─────────────┐
│   Claude    │ ───────────────→│    DAKB     │
│  Machine 1  │   session_id    │   Server    │
└─────────────┘                 └──────┬──────┘
                                       │
                                Import │ session_id
                                       ▼
                                ┌─────────────┐
                                │    GPT      │
                                │  Machine 2  │
                                └─────────────┘
```

**Track session status anytime:**
```python
dakb_advanced(operation="session_status")
# → Shows: active sessions, git context, recent activity
```

---

## Best Practices for Context Management

### 1. Store Decisions, Not Just Facts
```python
# Bad: Just the fact
"We use MongoDB"

# Good: The decision with reasoning
"We chose MongoDB because: 1) time-series fits document model,
2) flexible schema for evolving indicators, 3) aggregation pipeline
for analytics. Considered PostgreSQL but rejected due to..."
```

### 2. Use Consistent Tagging
```python
tags=[
    "project-trading-bot",  # Project identifier
    "component-data-fetch", # Component
    "type-architecture",    # Type of knowledge
    "status-current"        # Status
]
```

### 3. Store Incrementally
Don't wait until the end of a session. Store as you go:
```python
# After each significant discovery
dakb_store_knowledge(...)

# At natural breakpoints
dakb_store_knowledge(
    title="Session Summary - 2026-01-07",
    content="What we accomplished today...",
    tags=["session-summary", "2026-01-07"]
)
```

### 4. Search Before Starting
Begin each session by loading context:
```python
# Start of new session
results = dakb_search("trading bot recent progress")
results = dakb_search("open issues ML training")
```

### 5. Use Sessions for Work Tracking
```python
# Start a tracked session
dakb_advanced(operation="session_start", params={
    "description": "Implementing new reward function",
    "git_branch": "feature/reward-v2"
})

# End with summary
dakb_advanced(operation="session_end", params={
    "summary": "Completed reward function, tests passing"
})
```

---

## The Result

With DAKB managing your context:

| Before DAKB | After DAKB |
|-------------|------------|
| Re-explain project every session | Search and retrieve instantly |
| Lose insights when context fills | Permanent knowledge storage |
| Manual knowledge transfer | Automatic agent sharing |
| Rediscover bugs repeatedly | Search past solutions |
| Context locked to one machine | Distributed across your network |
| One agent, one LLM | Any agent, any LLM, any machine |
| You are the "human relay" | Agents share directly |

### The Core Value

**Your project knowledge lives in a shared, searchable layer** — not locked in markdown files on one machine, not trapped in one agent's context window.

- **Claude on your laptop** stores a pattern
- **GPT on your colleague's machine** finds it tomorrow
- **Gemini on a cloud server** uses it next week

Knowledge accumulates across your entire agent ecosystem. Your AI agents become smarter over time — together.

---

## Quick Start

```bash
# Install DAKB
git clone https://github.com/oracleseed/dakb.git
cd dakb && docker-compose -f docker/docker-compose.yml up -d

# Add to Claude Code (.mcp.json)
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

**Start storing knowledge today. Your future self will thank you.**

---

*Questions? Open an issue on [GitHub](https://github.com/oracleseed/dakb) or discuss in the Claude Code community.*
