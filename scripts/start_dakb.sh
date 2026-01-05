#!/bin/bash
#
# DAKB Service Startup Script
#
# This script starts all DAKB services in the correct order:
# 1. Embedding Server (port 3101, loopback only)
# 2. Gateway Server (port 3100)
#
# Usage:
#   ./start_dakb.sh [--foreground]
#
# Environment Variables:
#   DAKB_INTERNAL_SECRET - Required for embedding service auth
#   DAKB_JWT_SECRET - Required for gateway JWT auth (auto-generated if not set)
#   MONGO_URI - MongoDB connection string
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
LOG_DIR="$DAKB_ROOT/logs"
PID_DIR="$DAKB_ROOT/pids"
VENV_PATH="$PROJECT_ROOT/venv"

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

# Parse arguments
FOREGROUND=false
if [ "$1" == "--foreground" ]; then
    FOREGROUND=true
fi

# Create required directories
mkdir -p "$LOG_DIR"
mkdir -p "$PID_DIR"
mkdir -p "$DAKB_ROOT/secrets"
SECRETS_FILE="$DAKB_ROOT/secrets/.dakb_secrets"

# Activate virtual environment
if [ -d "$VENV_PATH" ]; then
    log_info "Activating virtual environment: $VENV_PATH"
    source "$VENV_PATH/bin/activate"
else
    log_warn "Virtual environment not found at $VENV_PATH"
    log_warn "Using system Python"
fi

# Add project to PYTHONPATH
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

# Load secrets from canonical file - NEVER auto-generate to avoid token invalidation
if [ -f "$SECRETS_FILE" ]; then
    log_info "Loading secrets from: $SECRETS_FILE"
    source "$SECRETS_FILE"
else
    log_error "Secrets file not found: $SECRETS_FILE"
    log_error "Create it with DAKB_INTERNAL_SECRET and DAKB_JWT_SECRET"
    log_error "These MUST match settings.json for existing tokens to work!"
    exit 1
fi

# Validate required secrets are set
if [ -z "$DAKB_INTERNAL_SECRET" ]; then
    log_error "DAKB_INTERNAL_SECRET not set in $SECRETS_FILE"
    exit 1
fi

if [ -z "$DAKB_JWT_SECRET" ]; then
    log_error "DAKB_JWT_SECRET not set in $SECRETS_FILE"
    exit 1
fi

log_info "Secrets loaded successfully (JWT secret: ${DAKB_JWT_SECRET:0:10}...)"

# Check MongoDB connection
log_info "Checking MongoDB connection..."
python3 -c "
import os
from pymongo import MongoClient
uri = os.getenv('MONGO_URI', 'mongodb://localhost:27017/dakb')
client = MongoClient(uri, serverSelectionTimeoutMS=5000)
client.admin.command('ping')
print('MongoDB connection successful')
" || {
    log_error "MongoDB connection failed. Check MONGO_URI environment variable."
    exit 1
}

# Function to check if port is available
check_port() {
    local port=$1
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        return 1
    fi
    return 0
}

# Check ports
if ! check_port 3101; then
    log_warn "Port 3101 (Embedding Server) is already in use"
    EMBEDDING_PID=$(lsof -Pi :3101 -sTCP:LISTEN -t 2>/dev/null)
    log_info "Existing process PID: $EMBEDDING_PID"
fi

if ! check_port 3100; then
    log_warn "Port 3100 (Gateway Server) is already in use"
    GATEWAY_PID=$(lsof -Pi :3100 -sTCP:LISTEN -t 2>/dev/null)
    log_info "Existing process PID: $GATEWAY_PID"
fi

# Start Embedding Server (if not running)
if check_port 3101; then
    log_info "Starting Embedding Server on port 3101 (loopback only)..."

    if [ "$FOREGROUND" = true ]; then
        # Run in foreground (for debugging)
        python3 -m backend.dakb_service.embeddings.embedding_server
    else
        # Run in background
        nohup python3 -m backend.dakb_service.embeddings.embedding_server \
            > "$LOG_DIR/embedding_server.log" 2>&1 &
        EMBEDDING_PID=$!
        echo $EMBEDDING_PID > "$PID_DIR/embedding_server.pid"
        log_info "Embedding Server started (PID: $EMBEDDING_PID)"

        # Wait for startup
        sleep 2

        # Check if still running
        if ! kill -0 $EMBEDDING_PID 2>/dev/null; then
            log_error "Embedding Server failed to start. Check logs:"
            tail -20 "$LOG_DIR/embedding_server.log"
            exit 1
        fi
    fi
else
    log_info "Embedding Server already running"
fi

# Wait for embedding server to be ready
log_info "Waiting for Embedding Server to be ready..."
for i in {1..30}; do
    if curl -s http://127.0.0.1:3101/health > /dev/null 2>&1; then
        log_info "Embedding Server is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        log_error "Embedding Server health check timeout"
        exit 1
    fi
    sleep 1
done

# Start Gateway Server (if not running)
if check_port 3100; then
    log_info "Starting Gateway Server on port 3100..."

    if [ "$FOREGROUND" = true ]; then
        log_error "Cannot run Gateway in foreground when Embedding is also in foreground"
        exit 1
    fi

    # Run in background
    nohup python3 -m backend.dakb_service.gateway.main \
        > "$LOG_DIR/gateway_server.log" 2>&1 &
    GATEWAY_PID=$!
    echo $GATEWAY_PID > "$PID_DIR/gateway_server.pid"
    log_info "Gateway Server started (PID: $GATEWAY_PID)"

    # Wait for startup
    sleep 2

    # Check if still running
    if ! kill -0 $GATEWAY_PID 2>/dev/null; then
        log_error "Gateway Server failed to start. Check logs:"
        tail -20 "$LOG_DIR/gateway_server.log"
        exit 1
    fi
else
    log_info "Gateway Server already running"
fi

# Wait for gateway server to be ready
log_info "Waiting for Gateway Server to be ready..."
for i in {1..30}; do
    if curl -s http://localhost:3100/health > /dev/null 2>&1; then
        log_info "Gateway Server is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        log_error "Gateway Server health check timeout"
        exit 1
    fi
    sleep 1
done

# Print status
echo ""
log_info "============================================"
log_info "DAKB Services Started Successfully!"
log_info "============================================"
echo ""
log_info "Embedding Server: http://127.0.0.1:3101 (internal only)"
log_info "Gateway Server:   http://localhost:3100"
echo ""
# ISS-054 Fix: Don't print secrets to stdout
if [ -f "$SECRETS_FILE" ]; then
    log_info "Secrets file: $SECRETS_FILE (chmod 600)"
    log_info "To persist secrets: source $SECRETS_FILE"
fi
echo ""
log_info "Logs:"
echo "  Embedding: $LOG_DIR/embedding_server.log"
echo "  Gateway:   $LOG_DIR/gateway_server.log"
echo ""
log_info "To stop services, run:"
echo "  ./stop_dakb.sh"
echo ""
