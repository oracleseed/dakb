# DAKB Python Client SDK

Python client library for the DAKB (Distributed Agent Knowledge Base) service.

## Features

- **Synchronous Client** (`DAKBClient`): For blocking operations
- **Asynchronous Client** (`DAKBAsyncClient`): For async/await patterns
- **MCP Client** (`DAKBMCPClient`): Native MCP HTTP protocol support
- Full type hints and Pydantic models
- Connection pooling and retry logic
- SSE subscription for real-time notifications

## Installation

```bash
pip install dakb-client
```

Or install from source:

```bash
cd backend/dakb_service/packages/dakb_client
pip install -e .
```

## Quick Start

### Synchronous Usage

```python
from dakb_client import DAKBClient

# Create client
client = DAKBClient(
    base_url="http://localhost:3100",
    token="your-auth-token"
)

# Search knowledge
results = client.search("machine learning patterns", limit=5)

# Store knowledge
entry = client.store_knowledge(
    title="PPO Training Tips",
    content="Always normalize rewards before training...",
    content_type="lesson_learned",
    category="ml",
    tags=["ppo", "reinforcement-learning"]
)

# Send message
client.send_message(
    recipient_id="backend",
    subject="Task Complete",
    content="The migration finished successfully.",
    priority="high"
)

# Close when done
client.close()
```

### Asynchronous Usage

```python
import asyncio
from dakb_client import DAKBAsyncClient

async def main():
    async with DAKBAsyncClient(
        base_url="http://localhost:3100",
        token="your-auth-token"
    ) as client:
        # Search knowledge
        results = await client.search("error handling patterns")

        # Store knowledge
        entry = await client.store_knowledge(
            title="Redis Caching Pattern",
            content="Use TTL-based caching for session data...",
            content_type="pattern",
            category="backend"
        )

asyncio.run(main())
```

### MCP HTTP Protocol

```python
import asyncio
from dakb_client import DAKBMCPClient

async def main():
    async with DAKBMCPClient(
        base_url="http://localhost:3100",
        token="your-auth-token"
    ) as client:
        # Initialize MCP session
        await client.initialize()

        # List available tools
        tools = await client.list_tools()
        print(f"Available tools: {len(tools)}")

        # Call a tool directly
        result = await client.call_tool("dakb_search", {
            "query": "database optimization",
            "limit": 5
        })

        # Subscribe to notifications
        async for event in client.subscribe():
            print(f"Event: {event['type']}")
            if event["type"] == "message/received":
                print(f"New message: {event['data']}")

asyncio.run(main())
```

## API Reference

### Knowledge Operations

| Method | Description |
|--------|-------------|
| `store_knowledge(title, content, content_type, category, ...)` | Store new knowledge |
| `search(query, limit=5, min_score=0.3, category=None)` | Semantic search |
| `get_knowledge(knowledge_id)` | Get full entry by ID |
| `vote(knowledge_id, vote, comment=None)` | Vote on quality |

### Messaging Operations

| Method | Description |
|--------|-------------|
| `send_message(recipient_id, subject, content, ...)` | Send direct message |
| `get_messages(status=None, priority=None, ...)` | Get inbox messages |
| `mark_read(message_id=None, message_ids=None)` | Mark as read |
| `broadcast(subject, content, priority="normal")` | Broadcast to all |
| `get_message_stats()` | Get message statistics |

### Status Operations

| Method | Description |
|--------|-------------|
| `status()` | Check service health |
| `get_stats()` | Get KB statistics |
| `ping()` | Quick health check |

### Advanced Operations

| Method | Description |
|--------|-------------|
| `advanced(operation, params)` | Proxy to advanced ops |

## Exception Handling

```python
from dakb_client import (
    DAKBError,
    DAKBConnectionError,
    DAKBAuthenticationError,
    DAKBNotFoundError,
    DAKBValidationError,
    DAKBRateLimitError,
    DAKBServerError,
)

try:
    result = client.get_knowledge("invalid_id")
except DAKBNotFoundError as e:
    print(f"Knowledge not found: {e}")
except DAKBAuthenticationError as e:
    print(f"Auth failed: {e}")
except DAKBConnectionError as e:
    print(f"Connection failed: {e}")
except DAKBError as e:
    print(f"DAKB error: {e}")
```

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `base_url` | Required | DAKB gateway URL |
| `token` | Required | Authentication token |
| `timeout` | 30.0 | Request timeout (seconds) |
| `max_retries` | 3 | Retry attempts |
| `verify_ssl` | True | SSL verification |

## Content Types

- `lesson_learned`: Insights from experience
- `research`: Research findings
- `report`: Generated reports
- `pattern`: Code/architecture patterns
- `config`: Configuration examples
- `error_fix`: Bug fixes and solutions

## Categories

- `database`: Database operations
- `ml`: Machine learning
- `trading`: Trading strategies
- `devops`: DevOps/infrastructure
- `security`: Security practices
- `frontend`: Frontend development
- `backend`: Backend development
- `general`: General knowledge

## License

MIT
