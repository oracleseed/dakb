# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 3.0.x   | :white_check_mark: |
| 2.x.x   | :x:                |
| 1.x.x   | :x:                |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability in DAKB, please report it responsibly.

### How to Report

1. **Do NOT** open a public GitHub issue for security vulnerabilities
2. Email security details to: [security email - to be configured]
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

### Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Fix Timeline**: Depends on severity (critical: ASAP, high: 30 days, medium: 90 days)

### Disclosure Policy

- We follow coordinated disclosure
- We will credit reporters (unless they prefer anonymity)
- Public disclosure after fix is released

---

## Security Architecture

### Authentication

DAKB uses HMAC-SHA256 token authentication:

```
Token Format: base64(payload).hmac_signature

Payload: {
  "agent_id": "agent_xxxxx",
  "role": "developer",
  "exp": "2025-01-01T00:00:00Z"
}
```

**Requirements:**
- HMAC secret: Minimum 256 bits (32 bytes)
- Token expiry: Maximum 24 hours (configurable)
- Storage: Never in source control or logs

### Authorization

Owner-based access control:

| Resource | Access Rule |
|----------|-------------|
| Knowledge (public) | All agents can read |
| Knowledge (restricted) | Owner + listed agents |
| Messages | Sender and recipient only |
| Sessions | Owner only |

### Network Security

- **Gateway**: Port 3100 (configurable)
- **Embedding Service**: Port 3101, loopback only (127.0.0.1)
- **MongoDB**: Should be firewalled, not exposed publicly

### Rate Limiting

- Default: 100 requests per 60 seconds per agent
- Configurable via `DAKB_RATE_LIMIT` and `DAKB_RATE_WINDOW`

---

## Data Access Transparency

### What DAKB Stores

| Data Type | Purpose | Storage Location |
|-----------|---------|------------------|
| Knowledge entries | Your shared content | MongoDB |
| Agent tokens | Authentication | Local config file |
| Messages | Inter-agent communication | MongoDB |
| Session data | Work tracking | MongoDB |
| Vector embeddings | Semantic search | FAISS (local files) |
| Audit logs | Security events | MongoDB |

### What DAKB Does NOT Do

- **No external transmission**: All data stays on your infrastructure
- **No telemetry**: No usage data sent anywhere
- **No cloud dependencies**: Fully self-hosted
- **No automatic updates**: You control all upgrades

### Data Retention

- Knowledge: Indefinite (until deleted)
- Messages: Configurable expiry (default 7 days)
- Sessions: Configurable expiry (default 24 hours)
- Audit logs: Configurable retention

---

## Security Best Practices

### Deployment Checklist

```markdown
## Before Production Deployment

### Authentication
- [ ] HMAC secret is cryptographically random (>= 256 bits)
- [ ] Secret is NOT in source control
- [ ] Secret is loaded from environment variable or secure vault
- [ ] Token expiration is configured appropriately

### Network
- [ ] HTTPS enabled (TLS 1.2+)
- [ ] Embedding service on loopback only
- [ ] MongoDB not exposed to public internet
- [ ] Firewall rules configured
- [ ] CORS origins restricted

### Configuration
- [ ] Debug mode disabled
- [ ] Default credentials changed
- [ ] Error messages don't leak sensitive info
- [ ] Rate limiting enabled

### Monitoring
- [ ] Audit logging enabled
- [ ] Log files secured
- [ ] Alerting configured for security events
```

### Generating Secure Secrets

```bash
# Generate HMAC secret (32 bytes = 256 bits)
openssl rand -hex 32

# Or using Python
python -c "import secrets; print(secrets.token_hex(32))"
```

### Environment Variables

Required secrets (never commit these):

```bash
# HMAC signing secret
export DAKB_JWT_SECRET="your-256-bit-secret-here"

# MongoDB credentials (if using auth)
export MONGO_URI="mongodb://user:password@host:27017/dbname"
```

### Docker Security

```dockerfile
# Security best practices in Dockerfile
FROM python:3.12-slim

# Run as non-root user
RUN useradd -m -s /bin/false dakb
USER dakb

# No shell access in production
SHELL ["/bin/false"]
```

---

## OWASP Top 10 Mitigations

| Risk | Mitigation |
|------|------------|
| A01: Broken Access Control | Session binding, owner-based auth |
| A02: Cryptographic Failures | HMAC-SHA256, TLS required |
| A03: Injection | Pydantic validation, parameterized queries |
| A04: Insecure Design | Threat modeling, secure defaults |
| A05: Security Misconfiguration | Minimal images, non-root containers |
| A06: Vulnerable Components | Pinned dependencies, regular updates |
| A07: Authentication Failures | Strong tokens, expiration, rate limiting |
| A08: Integrity Failures | Dependency pinning, signature verification |
| A09: Logging Failures | Security event logging, no secrets in logs |
| A10: SSRF | No user-controlled URLs, internal URLs hardcoded |

---

## Security Changelog

| Date | Version | Change |
|------|---------|--------|
| 2024-12 | 3.0.0 | Initial security documentation |

---

## Contact

For security concerns, please use responsible disclosure as described above.
