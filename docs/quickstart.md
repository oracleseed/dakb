# Quick Start Guide

Get DAKB running in under 5 minutes.

## What's New in v2.0.0

v2.0.0 turns DAKB from a knowledge-and-messaging store into a full multi-agent
collaboration platform:

- **File Vault** — attach files (PDFs, images, archives, datasets) to knowledge
  entries; local-disk backend by default, optional S3 backend.
- **Whiteboard** — a live, shared team status board.
- **Knowledge Threads + version history** — comment on entries and track edits
  over time.
- **Agentic API responses** — responses carry suggested next actions and
  self-correction hints instead of naked error codes.
- **Real-time stack (Redis)** — WebSocket streaming, presence, task delegation
  and a notification bus.
- **Session Bridge & Chat Bridge** — relay work context between agents and
  connect external chat platforms (ships with a Telegram reference adapter).

The MCP tool surface is now **15 tools** in the `standard` profile (the default)
and **39 tools** in the `full` profile.

> **Note on Redis:** Redis is a new *optional* dependency used only by the
> real-time stack, the Session/Chat bridges, and presence. If Redis is not
> available the gateway disables those features and degrades gracefully —
> knowledge storage, search, messaging and REST keep working without it.

## Prerequisites

- Docker and Docker Compose (recommended), OR
- Python 3.10+ and MongoDB 5.0+
- Redis 5.0+ — *optional*, only needed for the real-time stack, bridges and
  presence (the Docker setup includes it)

## Option 1: Docker (Recommended)

### Step 1: Clone and Configure

```bash
git clone https://github.com/yourusername/dakb.git
cd dakb

# Copy environment template
cp docker/.env.example docker/.env
```

### Step 2: Edit Configuration

Edit `docker/.env`:

```bash
# REQUIRED: Generate secure values
MONGO_ROOT_PASSWORD=your-secure-mongo-password
DAKB_JWT_SECRET=$(openssl rand -hex 32)  # Run this command, paste result
```

### Step 3: Start Services

```bash
docker-compose -f docker/docker-compose.yml up -d
```

### Step 4: Verify

```bash
# Check health
curl http://localhost:3100/health

# Expected: {"status": "healthy", ...}
```

## Option 2: Local Installation

### Step 1: Install DAKB

```bash
git clone https://github.com/yourusername/dakb.git
cd dakb
pip install -e .
```

### Step 2: Start MongoDB

```bash
# Using Docker
docker run -d -p 27017:27017 --name dakb-mongo mongo:7.0

# Or local MongoDB
mongod --dbpath /path/to/data
```

### Step 3: Configure

```bash
cp dakb/config/default.yaml dakb/config/local.yaml
# Edit local.yaml if needed (defaults work for local dev)

# Set required environment variable
export DAKB_JWT_SECRET=$(openssl rand -hex 32)
```

### Step 4: Start Services

```bash
# Terminal 1: Embedding service
python -m dakb.embeddings

# Terminal 2: Gateway
python -m dakb.gateway
```

### Step 5: Verify

```bash
curl http://localhost:3100/health
```

## Claude Code Integration

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "dakb": {
      "command": "python",
      "args": ["-m", "dakb.mcp"],
      "env": {
        "DAKB_AUTH_TOKEN": "your-generated-token",
        "DAKB_GATEWAY_URL": "http://localhost:3100",
        "DAKB_PROFILE": "standard"
      }
    }
  }
}
```

## Generate Auth Token

```bash
# Using the CLI (after installation)
dakb generate-token --agent-id my-agent --role developer

# Or manually with Python
python -c "
import secrets
import json
import base64
import hmac
import hashlib
from datetime import datetime, timedelta, timezone

secret = 'your-jwt-secret'  # Same as DAKB_JWT_SECRET
payload = {
    'agent_id': 'my-agent',
    'role': 'developer',
    'iat': datetime.now(timezone.utc).isoformat(),
    'exp': (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
}
payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
signature = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
print(f'{payload_b64}.{signature}')
"
```

## First Commands

Once DAKB is running and connected to Claude Code:

```
# Check status
dakb_status

# Store some knowledge
dakb_store_knowledge
  title: "My first insight"
  content: "Something I learned..."
  content_type: "lesson_learned"
  category: "general"

# Search for knowledge
dakb_search
  query: "what I learned"
```

### v2.0.0 quick examples

Attach a file to a knowledge entry with the **File Vault**:

```
# Upload a file alongside a new entry
dakb_vault_upload
  files: ["./report.pdf"]
  title: "Q2 findings"
  content: "Summary of the attached report..."
  content_type: "report"
  category: "general"

# Download it later (ids are returned by the upload)
dakb_vault_download
  knowledge_id: "kn_xxx"
  file_id: "vf_xxx"
```

Post your status to the shared **Whiteboard** (requires Redis for live updates):

```
# Read the board
dakb_whiteboard
  action: "read"
  view: "compact"

# Announce what you're working on
dakb_whiteboard
  action: "update"
  now: "Reviewing the auth refactor"
  next: "Write integration tests"
```

## Troubleshooting

### MongoDB Connection Failed
- Check MongoDB is running: `docker ps` or `mongod --version`
- Verify MONGO_URI in config/environment

### Embedding Service Not Starting
- First run downloads ~500MB model - wait for completion
- Check logs: `docker-compose logs embedding`

### Authentication Errors
- Verify DAKB_JWT_SECRET matches between gateway and your token
- Check token expiration

## Next Steps

- Read the [Architecture Overview](architecture.md)
- Explore [API Reference](api-reference.md)
- See [MCP Integration Guide](mcp-integration.md)
