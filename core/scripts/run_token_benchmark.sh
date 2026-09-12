#!/bin/bash
set -eo pipefail

EVENT_NAME="${GITHUB_EVENT_NAME:-push}"
CURRENT_REF="${GITHUB_REF_NAME:-main}"

if [ "$EVENT_NAME" != "release" ]; then
    echo "Not a release. Generating stubs for PR/push."
    cat << 'JSON' > baseline.jsonl
{"session_id": "sess-baseline-1", "weighted": 15000, "turns": 5, "model": "stub-model", "timestamp": 1700000000}
{"session_id": "sess-baseline-2", "weighted": 8000, "turns": 3, "model": "stub-model", "timestamp": 1700000100}
JSON
    cat << 'JSON' > post.jsonl
{"session_id": "sess-post-1", "weighted": 12000, "turns": 4, "model": "stub-model", "timestamp": 1700000200}
{"session_id": "sess-post-2", "weighted": 7500, "turns": 3, "model": "stub-model", "timestamp": 1700000300}
JSON
    exit 0
fi

MODEL="claude-3-5-haiku-20241022"
# Task chosen to be simple, repetitive, and unblocked by the 350-line read-guard limit.
TASK="read core/scripts/token_telemetry_report.py and output a summary"
TASK_HASH=$(echo -n "$MODEL:$TASK:3" | sha256sum | awk '{print $1}' | cut -c 1-8)

git fetch --tags --force
PREV_TAG=$(git describe --tags --abbrev=0 "$CURRENT_REF^" 2>/dev/null || echo "")

if [ -z "$PREV_TAG" ]; then
    echo "Error: No previous tag found. Cannot perform A/B test."
    exit 1
fi

echo "Comparing $PREV_TAG (A) vs $CURRENT_REF (B)"

MANIFEST_PATH="docs/marc/benchmarks/$PREV_TAG/manifest.json"
BASELINE_PATH="docs/marc/benchmarks/$PREV_TAG/baseline.jsonl"
RERUN_A=true

if [ -f "$MANIFEST_PATH" ] && [ -f "$BASELINE_PATH" ]; then
    CACHED_HASH=$(python3 -c "import json, sys; print(json.load(open(sys.argv[1])).get('task_hash', ''))" "$MANIFEST_PATH")
    if [ "$CACHED_HASH" == "$TASK_HASH" ]; then
        echo "Manifest matches! Reusing baseline."
        cp "$BASELINE_PATH" baseline.jsonl
        RERUN_A=false
    else
        echo "Manifest drift detected. Re-running arm A."
    fi
else
    echo "No cached baseline found for $PREV_TAG. Running arm A."
fi

# Clean up global state
npm i -g @anthropic-ai/claude-code

if [ "$RERUN_A" = true ]; then
    echo "--- RUNNING ARM A ($PREV_TAG) ---"
    git worktree add ../arm-a "$PREV_TAG"
    pushd ../arm-a
    
    export MARC_STATE_DIR="$HOME/.claude/marc-state-a"
    mkdir -p "$MARC_STATE_DIR"
    
    claude plugin marketplace add ./
    claude plugin install marc@nexaduo
    
    mkdir -p .agents
    echo "[telemetry]" > .agents/team.toml
    echo "enabled = true" >> .agents/team.toml
    
    # Arm A runs without optimizations
    for i in 1 2 3; do
        claude -m "$MODEL" -p "$TASK"
    done
    
    cp "$MARC_STATE_DIR/token-telemetry.jsonl" "$GITHUB_WORKSPACE/baseline.jsonl"
    popd
fi

echo "--- RUNNING ARM B ($CURRENT_REF) ---"
export MARC_STATE_DIR="$HOME/.claude/marc-state-b"
mkdir -p "$MARC_STATE_DIR"

claude plugin marketplace add ./
claude plugin install marc@nexaduo

mkdir -p .agents
echo "[telemetry]" > .agents/team.toml
echo "enabled = true" >> .agents/team.toml
# Enable optimizations for arm B
echo "[token_guard]" >> .agents/team.toml
echo "max_read_lines = 350" >> .agents/team.toml

for i in 1 2 3; do
    claude -m "$MODEL" -p "$TASK"
done

cp "$MARC_STATE_DIR/token-telemetry.jsonl" post.jsonl

# Save current run as baseline for future
mkdir -p "docs/marc/benchmarks/$CURRENT_REF"
cp post.jsonl "docs/marc/benchmarks/$CURRENT_REF/baseline.jsonl"
cat << JSON > "docs/marc/benchmarks/$CURRENT_REF/manifest.json"
{
  "model": "$MODEL",
  "task": "$TASK",
  "task_hash": "$TASK_HASH",
  "tag": "$CURRENT_REF"
}
JSON
