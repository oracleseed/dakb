#!/bin/bash
#
# DAKB Service Stop Script
#
# This script stops all DAKB services.
#
# Usage:
#   ./stop_dakb.sh
#
# Author: Backend Agent (Claude Opus 4.5)
# Version: 1.0
# Created: 2025-12-08
#

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DAKB_ROOT="$PROJECT_ROOT/backend/dakb_service"
PID_DIR="$DAKB_ROOT/pids"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Stop a service by PID file
stop_service() {
    local service_name=$1
    local pid_file=$2

    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 $pid 2>/dev/null; then
            log_info "Stopping $service_name (PID: $pid)..."
            kill $pid 2>/dev/null || true

            # Wait for graceful shutdown
            for i in {1..10}; do
                if ! kill -0 $pid 2>/dev/null; then
                    log_info "$service_name stopped"
                    rm -f "$pid_file"
                    return 0
                fi
                sleep 1
            done

            # Force kill if still running
            log_warn "$service_name did not stop gracefully, forcing..."
            kill -9 $pid 2>/dev/null || true
            rm -f "$pid_file"
        else
            log_info "$service_name is not running (stale PID file)"
            rm -f "$pid_file"
        fi
    else
        log_info "No PID file for $service_name"
    fi
}

# Stop services in reverse order
log_info "Stopping DAKB services..."

# Stop Gateway first
stop_service "Gateway Server" "$PID_DIR/gateway_server.pid"

# Then stop Embedding Server
stop_service "Embedding Server" "$PID_DIR/embedding_server.pid"

# Also kill any processes by port (in case PID files are missing)
log_info "Checking for orphaned processes..."

# Kill processes on port 3100 (Gateway)
GATEWAY_PID=$(lsof -Pi :3100 -sTCP:LISTEN -t 2>/dev/null || true)
if [ -n "$GATEWAY_PID" ]; then
    log_warn "Found orphaned Gateway process on port 3100 (PID: $GATEWAY_PID)"
    kill $GATEWAY_PID 2>/dev/null || true
fi

# Kill processes on port 3101 (Embedding)
EMBEDDING_PID=$(lsof -Pi :3101 -sTCP:LISTEN -t 2>/dev/null || true)
if [ -n "$EMBEDDING_PID" ]; then
    log_warn "Found orphaned Embedding process on port 3101 (PID: $EMBEDDING_PID)"
    kill $EMBEDDING_PID 2>/dev/null || true
fi

log_info "DAKB services stopped"
