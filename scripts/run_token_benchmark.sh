#!/bin/bash
set -eo pipefail

EVENT_NAME="${GITHUB_EVENT_NAME:-push}"
CURRENT_REF="${GITHUB_REF_NAME:-main}"
GITHUB_WORKSPACE="${GITHUB_WORKSPACE:-$PWD}"

MODEL="claude-sonnet-5"
# Target a file larger than 350 lines so read-guard actually fires
TASK="read core/scripts/board.py and output a summary"

# --- Testable helpers (defined before main(); see the sourcing guard at the
# bottom of this file). Keeping these as standalone functions lets a companion
# test source this script (which then does NOT execute main()) and exercise
# them directly with stubbed `claude`/`python3` state, without a real release
# environment (tag, CLI, credentials). ---------------------------------------

# resolve_claude_version: capture `claude --version`. MUST be called after the
# CLI is actually installed (see main(), below) — a failure here means the
# CLI is genuinely missing/broken post-install, which is a real environment
# error. We deliberately do NOT swallow it into a placeholder like "unknown":
# doing so would silently defeat the drift-guard hash that depends on this
# value (issue #281).
resolve_claude_version() {
    claude --version 2>/dev/null || {
        echo "Error: 'claude --version' failed after CLI install. Refusing to hash a placeholder 'unknown' version — this would silently disable the drift guard." >&2
        return 1
    }
}

# compute_task_hash: pure function, no side effects.
compute_task_hash() {
    local claude_version="$1" model="$2" task="$3"
    echo -n "$claude_version:$model:$task:3" | sha256sum | awk '{print $1}' | cut -c 1-8
}

# ensure_marketplace_added: idempotent `claude plugin marketplace add`.
#
# `claude plugin marketplace add "$source"` is called once per arm (arm A,
# inside the ../arm-a worktree, and arm B/C in the current workspace) because
# each arm needs the marketplace re-pointed at ITS OWN checkout — a blind
# `|| true` (like the neighbouring `git worktree add ... || true`) would risk
# masking a genuine failure (network, bad path, permissions) on the second
# call, and would still leave the marketplace pointed at the wrong source if
# the CLI's add-on-existing-name behavior ever isn't an upsert.
#
# Instead: check `claude plugin marketplace list --json` for an entry with
# this name BEFORE adding. If one exists, remove it explicitly so the
# subsequent `add` is guaranteed to (re)point the name at $source rather than
# relying on undocumented add-when-exists semantics.
#
# IMPORTANT (issue #281 PR #283 review, `@rev` finding, HIGH): `claude plugin
# marketplace list --json` is captured into a variable with `|| echo '[]'`
# BEFORE it is ever piped into python3, specifically so its own exit status
# never takes part in the `set -eo pipefail` pipeline. An empty/failing list
# on the very first call (nothing registered yet, e.g. arm A's first-ever
# call) must read as an ordinary "not registered" outcome, not a fatal error
# that kills the whole release before it ever reaches `add`. Genuine `remove`/
# `add` failures still propagate normally (no `|| true` around those).
#
# Also (`@sec` finding, LOW, open point resolved): if `remove` succeeds but
# the following `add` then fails (transient network error, bad path), the
# entry would otherwise be left deleted — worse than the old code's failure
# mode, which at least left the stale-but-valid prior entry intact. We
# snapshot the previous entry's source before removing it and, if the re-add
# fails, attempt to restore that previous registration before propagating the
# original failure.
ensure_marketplace_added() {
    local source="$1"
    local name
    name="$(python3 -c "
import json, sys
print(json.load(open(sys.argv[1] + '/.claude-plugin/marketplace.json'))['name'])
" "$source")" || {
        echo "Error: could not read marketplace name from $source/.claude-plugin/marketplace.json" >&2
        return 1
    }

    local list_json
    list_json="$(claude plugin marketplace list --json 2>/dev/null || echo '[]')"
    [ -n "$list_json" ] || list_json='[]'

    local existing_source
    if existing_source="$(python3 -c "
import json, sys
list_json, name = sys.argv[1], sys.argv[2]
try:
    entries = json.loads(list_json)
except Exception:
    entries = []
for e in entries:
    if e.get('name') == name:
        print(e.get('path') or e.get('repo') or '')
        sys.exit(0)
sys.exit(1)
" "$list_json" "$name")"; then
        echo "Marketplace '$name' already registered (was: ${existing_source:-unknown source}); removing stale entry so it re-points at $source"
        claude plugin marketplace remove "$name"

        if ! claude plugin marketplace add "$source"; then
            echo "Error: re-adding marketplace '$name' at $source failed after removing the stale entry." >&2
            if [ -n "$existing_source" ]; then
                echo "Attempting to restore the previous registration at $existing_source ..." >&2
                if claude plugin marketplace add "$existing_source"; then
                    echo "Restored previous registration; '$name' still points at $existing_source (NOT $source)." >&2
                else
                    echo "Error: restore attempt also failed; marketplace '$name' is now UNREGISTERED." >&2
                fi
            fi
            return 1
        fi
    else
        claude plugin marketplace add "$source"
    fi
}

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

main() {
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

    git fetch --tags --force
    PREV_TAG=$(git describe --tags --abbrev=0 "$CURRENT_REF^" 2>/dev/null || echo "")

    if [ -z "$PREV_TAG" ]; then
        echo "Error: No previous tag found. Cannot perform A/B test."
        exit 1
    fi

    echo "Comparing $PREV_TAG (A) vs $CURRENT_REF (B)"

    # Install the CLI here, AFTER the (cheap) previous-tag resolution above but
    # BEFORE anything that hashes or otherwise depends on its version. This is
    # a deliberate reordering fix for issue #281 (item 3): the old script
    # captured `claude --version` at the top of the file, before this install
    # step ever ran, so on a clean runner it always hashed the constant
    # "unknown" and the drift guard never fired.
    #
    # We evaluated the alternative (leave the install where it was, move the
    # hash computation down after it) and rejected it: the cache-reuse
    # decision right below already needs a correct, final TASK_HASH, so that
    # alternative would just relocate the same ordering constraint one block
    # later for no benefit. Installing right here also avoids installing the
    # CLI at all when the script is about to exit above for lack of a previous
    # tag.
    npm i -g @anthropic-ai/claude-code

    CLAUDE_VERSION=$(resolve_claude_version) || exit 1
    TASK_HASH=$(compute_task_hash "$CLAUDE_VERSION" "$MODEL" "$TASK")

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

    if [ "$RERUN_A" = true ]; then
        echo "--- RUNNING ARM A ($PREV_TAG) ---"
        git worktree add ../arm-a "$PREV_TAG" || true
        pushd ../arm-a

        ensure_marketplace_added "./"
        claude plugin install marc@nexaduo

        mkdir -p .agents
        echo "[telemetry]" > .agents/team.toml
        echo "enabled = true" >> .agents/team.toml

        rm -f "$GITHUB_WORKSPACE/baseline.jsonl"
        run_claude_safely "$GITHUB_WORKSPACE/baseline.jsonl" "$HOME/.claude/marc-state-a"

        popd
    fi

    echo "--- RUNNING ARM B ($CURRENT_REF with guard=350) ---"
    ensure_marketplace_added "./"
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
}

# Only run main() when executed directly, not when sourced by the companion
# test (scripts/test_run_token_benchmark.sh), which needs the functions above
# defined without the release pipeline actually running.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
