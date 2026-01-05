#!/bin/bash
#
# DAKB GitHub Publishing Script
#
# This script handles GitHub authentication, repository creation,
# and initial code push for the DAKB open-source project.
#
# Usage: ./github_publish.sh
#
# Author: Claude Code (Opus 4.5)
# Created: 2026-01-05
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
REPO_NAME="dakb"
REPO_DESCRIPTION="Distributed Agent Knowledge Base - Multi-agent collaboration system for Claude Code with semantic search, messaging, and session management"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

cd "$PROJECT_ROOT"

echo ""
echo "============================================"
echo "  DAKB GitHub Publishing Script"
echo "============================================"
echo ""

# Step 1: Check GitHub CLI authentication
log_step "1/4 Checking GitHub CLI authentication..."
if ! gh auth status &>/dev/null; then
    log_warn "Not authenticated with GitHub CLI"
    log_info "Starting browser authentication..."
    echo ""
    gh auth login --hostname github.com --git-protocol https --web

    if ! gh auth status &>/dev/null; then
        log_error "Authentication failed. Please try again."
        exit 1
    fi
fi
log_info "GitHub authentication: OK"

# Step 2: Check if repo already exists
log_step "2/4 Checking if repository exists..."
if gh repo view "oracleseed/$REPO_NAME" &>/dev/null; then
    log_warn "Repository oracleseed/$REPO_NAME already exists"
    read -p "Do you want to use the existing repository? (y/n): " USE_EXISTING
    if [ "$USE_EXISTING" != "y" ]; then
        log_error "Aborting. Please rename the existing repo or choose a different name."
        exit 1
    fi
else
    # Create repository
    log_step "3/4 Creating GitHub repository..."
    gh repo create "$REPO_NAME" \
        --public \
        --description "$REPO_DESCRIPTION" \
        --source . \
        --remote origin \
        --push=false
    log_info "Repository created: https://github.com/oracleseed/$REPO_NAME"
fi

# Step 3: Add remote and push
log_step "4/4 Pushing code to GitHub..."

# Check if origin already exists
if git remote get-url origin &>/dev/null; then
    CURRENT_URL=$(git remote get-url origin)
    if [ "$CURRENT_URL" != "https://github.com/oracleseed/$REPO_NAME.git" ]; then
        log_warn "Updating remote origin URL..."
        git remote set-url origin "https://github.com/oracleseed/$REPO_NAME.git"
    fi
else
    git remote add origin "https://github.com/oracleseed/$REPO_NAME.git"
fi

# Push to main branch
git push -u origin main

echo ""
echo "============================================"
log_info "SUCCESS! DAKB is now on GitHub!"
echo "============================================"
echo ""
echo "Repository URL: https://github.com/oracleseed/$REPO_NAME"
echo ""
echo "Next steps:"
echo "  1. Add topics: ai, claude, mcp, knowledge-base, multi-agent"
echo "  2. Enable GitHub Discussions for community Q&A"
echo "  3. Post to Claude Code Discord community"
echo ""
echo "Discord post template is ready at: docs/COMMUNITY_POST.md"
echo ""
