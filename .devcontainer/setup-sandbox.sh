#!/bin/bash
set -e

# ROOT is the workspace directory
ROOT="/workspaces/mARC"
SANDBOX="$ROOT/sandbox"

echo "=== Initializing sandbox workspace for manual testing ==="
mkdir -p "$SANDBOX"
cd "$SANDBOX"

# Initialize mock git repository
git init
git config --global --add safe.directory "$SANDBOX"
git config user.name "mARC Tester"
git config user.email "tester@marc.local"

# Create mock project files
echo "# Mock Project" > README.md
echo "This is a mock project to test the mARC plugin locally." >> README.md
mkdir -p src
echo "console.log('hello world');" > src/index.js

# Initial commit
git add .
git commit -m "initial commit"

echo "=== Pre-installing local mARC plugin in Claude Code & Antigravity ==="
# Setup local marketplace and install in Claude Code
export CLAUDE_CONFIG_DIR="/home/node/.claude-devcontainer"
mkdir -p "$CLAUDE_CONFIG_DIR"
claude plugin marketplace add "$ROOT"
claude plugin install marc@nexaduo

# Install in Antigravity
agy plugin install "$ROOT/harnesses/antigravity/marc"

echo "=== Sandbox ready! ==="
echo "To test:"
echo "1. Run: cd sandbox"
echo "2. Run: claude (or agy)"
echo "3. Run onboarding: /marc:init"
