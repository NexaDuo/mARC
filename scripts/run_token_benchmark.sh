#!/bin/bash
set -eo pipefail

EVENT_NAME="${GITHUB_EVENT_NAME:-push}"
CURRENT_REF="${GITHUB_REF_NAME:-main}"
GITHUB_WORKSPACE="${GITHUB_WORKSPACE:-$PWD}"

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
    cp baseline.jsonl toggle_baseline.jsonl
    cp post.jsonl toggle_post.jsonl
    exit 0
fi

MODEL="claude-3-5-sonnet-20241022"
# Target a file larger than 350 lines so read-guard actually fires
TASK="read core/scripts/board.py and output a summary"

# Hash claude --version to prevent drift
CLAUDE_VERSION=$(claude --version 2>/dev/null || echo "unknown")
TASK_HASH=$(echo -n "$CLAUDE_VERSION:$MODEL:$TASK:3" | sha256sum | awk '{print $1}' | cut -c 1-8)

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

run_claude_safely() {
    local target_file=$1
    local state_dir=$2
    for i in 1 2 3; do
        local temp_state="$state_dir/temp_run_$i"
        mkdir -p "$temp_state"
        export MARC_STATE_DIR="$temp_state"
        
        set +e
        claude --model "$MODEL" -p "$TASK"
        EXIT_CODE=$?
        set -e
        
        if [ $EXIT_CODE -eq 0 ] && [ -f "$temp_state/token-telemetry.jsonl" ]; then
            cat "$temp_state/token-telemetry.jsonl" >> "$target_file"
        else
            echo "Claude run $i failed with code $EXIT_CODE. Skipping telemetry."
        fi
    done
}

if [ "$RERUN_A" = true ]; then
    echo "--- RUNNING ARM A ($PREV_TAG) ---"
    git worktree add ../arm-a "$PREV_TAG" || true
    pushd ../arm-a
    
    claude plugin marketplace add ./
    claude plugin install marc@nexaduo
    
    mkdir -p .agents
    echo "[telemetry]" > .agents/team.toml
    echo "enabled = true" >> .agents/team.toml
    
    rm -f "$GITHUB_WORKSPACE/baseline.jsonl"
    run_claude_safely "$GITHUB_WORKSPACE/baseline.jsonl" "$HOME/.claude/marc-state-a"
    
    popd
fi

echo "--- RUNNING ARM B ($CURRENT_REF with guard=350) ---"
claude plugin marketplace add ./
claude plugin install marc@nexaduo

mkdir -p .agents
cat << CONFIG > .agents/team.toml
[telemetry]
enabled = true
[token_guard]
max_read_lines = 350
CONFIG

rm -f post.jsonl
run_claude_safely "$PWD/post.jsonl" "$HOME/.claude/marc-state-b"

echo "--- RUNNING ARM C ($CURRENT_REF with guard=999999) ---"
cat << CONFIG > .agents/team.toml
[telemetry]
enabled = true
[token_guard]
max_read_lines = 999999
CONFIG

rm -f toggle_baseline.jsonl
run_claude_safely "$PWD/toggle_baseline.jsonl" "$HOME/.claude/marc-state-c"

cp post.jsonl toggle_post.jsonl

# Save current run as baseline for future
mkdir -p "docs/marc/benchmarks/$CURRENT_REF"
cp post.jsonl "docs/marc/benchmarks/$CURRENT_REF/baseline.jsonl"
cat << JSON > "docs/marc/benchmarks/$CURRENT_REF/manifest.json"
{
  "model": "$MODEL",
  "task": "$TASK",
  "task_hash": "$TASK_HASH",
  "tag": "$CURRENT_REF",
  "claude_version": "$CLAUDE_VERSION"
}
JSON
