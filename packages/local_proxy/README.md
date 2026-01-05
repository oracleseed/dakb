# DAKB Local Proxy

A local MCP proxy for DAKB that bridges stdio-based MCP clients to the DAKB HTTP gateway.

## Features

- **MCP stdio Transport**: Connects legacy MCP clients (Claude Code, VS Code) to DAKB
- **Local Caching**: Reduces latency for frequently accessed knowledge
- **Connection Pooling**: Efficient HTTP connection management
- **CLI Management**: Easy start/stop/status commands

## Installation

```bash
pip install dakb-local-proxy
```

Or install from source:

```bash
cd backend/dakb_service/local_proxy
pip install -e .
```

## Quick Start

### 1. Set your auth token

```bash
export DAKB_AUTH_TOKEN="your-auth-token"
```

### 2. Start the proxy

```bash
dakb-proxy start
```

### 3. Configure your MCP client

For Claude Code, update `.mcp.json`:

```json
{
  "mcpServers": {
    "dakb": {
      "command": "dakb-proxy",
      "args": ["start"],
      "env": {
        "DAKB_AUTH_TOKEN": "your-token"
      }
    }
  }
}
```

## CLI Commands

### Start Proxy

```bash
# Basic start
dakb-proxy start

# With custom gateway
dakb-proxy start --gateway http://dakb.example.com:3100

# With custom port
dakb-proxy start --port 3111

# Disable caching
dakb-proxy start --no-cache

# Debug logging
dakb-proxy start --log-level DEBUG
```

### Stop Proxy

```bash
dakb-proxy stop
```

### Check Status

```bash
dakb-proxy status
```

### Cache Management

```bash
# Show cache stats
dakb-proxy cache stats

# Clear all caches
dakb-proxy cache clear
```

### Configuration

```bash
# Show current config
dakb-proxy config show

# Set a value
dakb-proxy config set connection.gateway_url http://dakb.example.com:3100
dakb-proxy config set cache.ttl_seconds 600

# Create default config
dakb-proxy config init
```

## Configuration

Configuration is loaded from multiple sources (in order of precedence):

1. **CLI arguments** (`--gateway`, `--port`, etc.)
2. **Environment variables** (`DAKB_PROXY_*`)
3. **Config file** (`~/.dakb/proxy.json`)
4. **Defaults**

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DAKB_AUTH_TOKEN` | Authentication token | Required |
| `DAKB_PROXY_GATEWAY_URL` | Gateway URL | http://localhost:3100 |
| `DAKB_PROXY_PORT` | Local proxy port | 3110 |
| `DAKB_PROXY_CACHE_ENABLED` | Enable caching | true |
| `DAKB_PROXY_CACHE_TTL` | Cache TTL (seconds) | 300 |
| `DAKB_PROXY_LOG_LEVEL` | Log level | INFO |
| `DAKB_PROXY_LOG_FILE` | Log file path | (stderr) |

### Config File

Create `~/.dakb/proxy.json`:

```json
{
  "connection": {
    "gateway_url": "http://localhost:3100",
    "timeout_seconds": 30,
    "max_retries": 3
  },
  "server": {
    "host": "127.0.0.1",
    "port": 3110,
    "stdio_enabled": true
  },
  "cache": {
    "enabled": true,
    "max_entries": 1000,
    "ttl_seconds": 300,
    "search_cache_ttl": 60
  },
  "logging": {
    "level": "INFO"
  }
}
```

## Caching Behavior

The proxy caches:

- **Search results**: 60 second TTL (configurable)
- **Knowledge entries**: 300 second TTL (configurable)

Cache keys are generated from request parameters:
- Search: query + limit + min_score + category
- Knowledge: knowledge_id

Cache is invalidated:
- After TTL expiration
- When max entries exceeded (LRU eviction)
- Manually via `dakb-proxy cache clear`

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    MCP Client                           │
│              (Claude Code, VS Code)                     │
└────────────────────────┬────────────────────────────────┘
                         │ stdio (JSON-RPC)
                         ▼
┌─────────────────────────────────────────────────────────┐
│                 DAKB Local Proxy                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ stdio I/O   │  │ Local Cache │  │ HTTP Client │     │
│  │   Handler   │──│  (LRU+TTL)  │──│   (httpx)   │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
└────────────────────────────────────────────────────────┘
                         │ HTTP
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   DAKB Gateway                          │
│                 (Port 3100)                             │
└─────────────────────────────────────────────────────────┘
```

## Troubleshooting

### Proxy not starting

1. Check auth token: `echo $DAKB_AUTH_TOKEN`
2. Check gateway availability: `curl http://localhost:3100/api/v1/status`
3. Check logs: `dakb-proxy start --log-level DEBUG`

### Connection errors

1. Verify gateway URL: `dakb-proxy config show`
2. Check network connectivity
3. Increase timeout: `dakb-proxy config set connection.timeout_seconds 60`

### Cache issues

1. Clear cache: `dakb-proxy cache clear`
2. Disable cache: `dakb-proxy start --no-cache`
3. Check cache stats: `dakb-proxy cache stats`

## License

MIT
