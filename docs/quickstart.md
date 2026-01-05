# Quick Start Guide

Get DAKB running in under 5 minutes.

## Prerequisites

- Docker and Docker Compose (recommended), OR
- Python 3.10+ and MongoDB 5.0+

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
