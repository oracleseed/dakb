# Managing Context in Large Projects with DAKB

## The Context Problem

When working with AI agents on large projects or deep research tasks, you'll hit a wall: **context limits**.

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
       → Agent A finds a bug pattern.
       → Agent B encounters same bug. Re-discovers it.
       → No knowledge sharing between sessions or agents.
```

**The result**: You become the "knowledge transfer bottleneck" — constantly re-explaining context, re-discovering solutions, losing insights when conversations end.

---

## How DAKB Solves This

DAKB creates a **persistent knowledge layer** that survives beyond any single conversation:

```
┌─────────────────────────────────────────────────────────────┐
│                    DAKB Knowledge Base                       │
│                                                              │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────┐   │
│  │ Project        │ │ Research       │ │ Error Fixes    │   │
│  │ Architecture   │ │ Findings       │ │ & Patterns     │   │
│  └────────────────┘ └────────────────┘ └────────────────┘   │
│                                                              │
│  Persists across sessions • Searchable • Shareable          │
└─────────────────────────────────────────────────────────────┘
         │                    │                    │
    ┌────▼────┐          ┌────▼────┐          ┌────▼────┐
    │Session 1│          │Session 5│          │Session N│
    │ Day 1   │          │ Week 2  │          │ Month 3 │
    └─────────┘          └─────────┘          └─────────┘
```

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

## Use Case 3: Multi-Agent Collaboration

### The Challenge
- Different agents handle different tasks
- Backend agent, ML agent, Research agent
- Need to share findings without human relay

### DAKB Solution

**Agent A discovers a pattern:**
```python
# Backend Agent finds an API issue
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

**Agent B finds it automatically:**
```python
# ML Agent hitting same issue days later
results = dakb_search("kraken api rate limit")
# → Finds the pattern without human intervention
```

**Cross-agent messaging for urgent issues:**
```python
# Backend Agent alerts ML Agent
dakb_send_message(
    recipient_id="ml-agent",
    subject="Kraken API Changes",
    content="API response format changed. Update your data parser.",
    priority="high"
)
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
| Context is bottleneck | Context is asset |

**Your AI agents become smarter over time** — they accumulate knowledge, learn from past mistakes, and build on previous work.

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
