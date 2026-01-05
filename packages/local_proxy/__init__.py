"""
DAKB Local Proxy

A local MCP proxy for DAKB that provides:
- MCP stdio transport for legacy clients (Claude Code, VS Code extensions)
- Local caching for frequently accessed knowledge
- Connection pooling to DAKB gateway
- CLI for easy management

Version: 1.0.0
Created: 2025-12-17
Session: sess_20251217_174528_23429cfe
Phase: 4 - Local Proxy Package

Usage:
    # Start proxy server
    dakb-proxy start

    # Check status
    dakb-proxy status

    # Stop proxy
    dakb-proxy stop

    # Clear cache
    dakb-proxy cache clear
"""

__version__ = "1.0.0"
__author__ = "DAKB Team"

from .config import ProxyConfig
from .cache import LocalCache

__all__ = [
    "__version__",
    "ProxyConfig",
    "LocalCache",
]
