#!/bin/bash
# `cond && pass ... || fail ...` is safe here: pass() always returns 0.
# shellcheck disable=SC2015
# Regression test for issue #346: the Codex arm of run_token_benchmark.sh.
#
# Offline, zero cost. Fake `codex` and `claude` binaries stand in for the
# real CLIs; nothing here can reach a paid model. Asserts:
#   1. Gating: every combination short of workflow_dispatch + real_run=true +
#      LOCAL_RUN=true + BENCH_HARNESS=codex takes the free stub path and never
#      calls `codex exec` (the paid call).
#   2. The default (Claude) stub path also writes codex_* stub files, and
#      benchmark_report.py prints the Codex section from them.
#   3. The real path, end to end against the fakes: previous-release
#      resolution + arm-A worktree, a per-arm isolated CODEX_HOME whose
#      auth.json is a SYMLINK to the login (never a copy, never printed),
#      plugin installed from each ref's own checkout, 2N+1 operator runs
#      (preflight + N per arm), specialist tokens by role and round, report.
#   4. A token refresh that replaces the auth.json symlink stops the run
#      before further spend, prints the recovery steps, and leaves the
#      scratch dir (holding the refreshed token) in place (SEC-4, PR #347).
#   5. A failed `codex exec` still has its rollouts moved out of CODEX_HOME,
#      so the next sample's specialist count is unchanged (REV-1, PR #347).
#   6. A `claude` that resolves to a relative path fails fast, before any
#      `codex exec` (SEC-1, PR #347).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/run_token_benchmark.sh"
REPORT="$SCRIPT_DIR/benchmark_report.py"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/tmp" "$WORK/auth" "$WORK/claude-config"
SENTINEL="SENTINEL-not-a-real-credential-346"
printf '{"token": "%s"}\n' "$SENTINEL" > "$WORK/auth/auth.json"
CALLS="$WORK/calls.log"

# --- fake codex ---------------------------------------------------------------
cat > "$WORK/bin/codex" << 'STUB'
#!/bin/bash
echo "codex $*" >> "$FAKE_CALLS"
case "$1" in
    --version) echo "codex-cli 0.0.0-fake"; exit 0 ;;
    plugin) echo "CODEX_HOME=$CODEX_HOME $*" >> "$FAKE_CALLS"; echo '{}'; exit 0 ;;
    exec) ;;
    *) exit 2 ;;
esac
prompt="${*: -1}"
n=$(( $(cat "$FAKE_CALLS.n" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$FAKE_CALLS.n"
tid="thread-$RANDOM$RANDOM"
python3 "$FAKE_ROLLOUT" "$CODEX_HOME" "$tid" "Fresh operator thread."
if [[ "$prompt" == *tech-lead* ]]; then
    claude --dangerously-skip-permissions --agent security -p "review"
    claude --dangerously-skip-permissions --agent review -p "review"
    python3 "$FAKE_ROLLOUT" "$CODEX_HOME" "dev-$tid" "Act as the mARC engineer specialist. Fix it."
fi
if [ "${FAKE_ROTATE:-0}" = "1" ]; then
    printf 'rotated\n' > "$CODEX_HOME/auth.json.tmp" && mv -f "$CODEX_HOME/auth.json.tmp" "$CODEX_HOME/auth.json"
fi
echo "{\"type\":\"thread.started\",\"thread_id\":\"$tid\"}"
if [ "$n" = "${FAKE_FAIL_AT:-0}" ]; then echo "fake failure" >&2; exit 1; fi
echo '{"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}'
STUB

cat > "$WORK/rollout.py" << 'PY'
import json, os, sys
home, tid, first = sys.argv[1:4]
d = os.path.join(home, "sessions", "2026", "01", "01")
os.makedirs(d, exist_ok=True)
rows = [{"type": "session_meta", "payload": {"id": tid, "timestamp": "2026-01-01T00:00:00Z"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": first}}]
tot = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
for i, (inp, cached, out) in enumerate([(1000, 0, 50), (1100, 1000, 20)]):
    u = {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}
    tot = {k: tot[k] + u[k] for k in tot}
    rows.append({"type": "token_usage_record", "payload": {"response_id": f"{tid}-{i}", "turn_id": "t1",
                 "usage": u, "thread_token_usage": dict(tot)}})
with open(os.path.join(d, f"rollout-2026-01-01T00-00-00-{tid}.jsonl"), "w") as fh:
    fh.write("\n".join(json.dumps(r) for r in rows) + "\n")
PY

# --- fake claude ----------------------------------------------------------------
cat > "$WORK/bin/claude" << 'STUB'
#!/bin/bash
echo "claude $*" >> "$FAKE_CALLS"
if [ "$1" = "--version" ]; then echo "0.0.0 (fake)"; exit 0; fi
if [ "$1" = "plugin" ]; then
    [ "$2 $3" = "marketplace list" ] && echo '[]'
    exit 0
fi
sid=""; prev=""
for a in "$@"; do [ "$prev" = "--session-id" ] && sid="$a"; prev="$a"; done
if [ -n "$sid" ]; then
    mkdir -p "$CLAUDE_CONFIG_DIR/projects/fixture"
    for _ in 1 2; do
        echo '{"type":"assistant","timestamp":"2026-01-01T00:00:01Z","message":{"id":"m1","usage":{"input_tokens":5,"cache_creation_input_tokens":20,"cache_read_input_tokens":100,"output_tokens":7}}}'
    done > "$CLAUDE_CONFIG_DIR/projects/fixture/$sid.jsonl"
fi
echo "done"
STUB
chmod +x "$WORK/bin/codex" "$WORK/bin/claude"

# --- a throwaway repo with two refs that both carry a (fake) Codex harness -----
REPO="$WORK/repo"
mkdir -p "$REPO/.agents/plugins" "$REPO/harnesses/codex/marc" "$REPO/.claude-plugin"
echo '{"name": "fakemkt", "plugins": []}' > "$REPO/.agents/plugins/marketplace.json"
echo '{"name": "fakemkt"}' > "$REPO/.claude-plugin/marketplace.json"
touch "$REPO/harnesses/codex/marc/.keep"
G=(git -C "$REPO" -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false -c tag.gpgsign=false)
"${G[@]}" -c init.defaultBranch=main init -q
"${G[@]}" add -A
"${G[@]}" commit -q -m one
"${G[@]}" tag v1.0.0
echo two > "$REPO/two.txt"
"${G[@]}" add two.txt
"${G[@]}" commit -q -m two

export FAKE_CALLS="$CALLS" FAKE_ROLLOUT="$WORK/rollout.py" TMPDIR="$WORK/tmp"
export PATH="$WORK/bin:$PATH"

run_bench() {  # run_bench <outdir> [VAR=value ...]
    local out="$1"; shift
    mkdir -p "$out"
    (cd "$REPO" && env -u GITHUB_OUTPUT GITHUB_WORKSPACE="$out" GITHUB_REF_NAME=main "$@" bash "$TARGET") > "$out/log.txt" 2>&1
}

exec_calls() { grep -c '^codex exec' "$CALLS" || true; }
reset_calls() { : > "$CALLS"; rm -f "$CALLS.n"; }

# 1. Gating matrix: none of these may reach `codex exec`.
n=0
for combo in \
    "GITHUB_EVENT_NAME=push BENCH_HARNESS=codex LOCAL_RUN=true REAL_RUN_INPUT=true" \
    "GITHUB_EVENT_NAME=pull_request BENCH_HARNESS=codex LOCAL_RUN=true REAL_RUN_INPUT=true" \
    "GITHUB_EVENT_NAME=workflow_dispatch BENCH_HARNESS=codex LOCAL_RUN=true REAL_RUN_INPUT=false" \
    "GITHUB_EVENT_NAME=workflow_dispatch BENCH_HARNESS=codex LOCAL_RUN=false REAL_RUN_INPUT=true" \
    "GITHUB_EVENT_NAME=workflow_dispatch BENCH_HARNESS=codex REAL_RUN_INPUT=TRUE LOCAL_RUN=true" \
    "GITHUB_EVENT_NAME=push"; do
    n=$((n + 1))
    reset_calls
    # shellcheck disable=SC2086
    if run_bench "$WORK/gate-$n" $combo && [ "$(exec_calls)" = 0 ] \
        && [ -s "$WORK/gate-$n/codex_baseline-scenario.jsonl" ] && [ -s "$WORK/gate-$n/codex_post-scenario.jsonl" ]; then
        pass "free path, no codex exec: $combo"
    else
        fail "gating: $combo (exec calls=$(exec_calls))"; cat "$WORK/gate-$n/log.txt"
    fi
done

# 2. Report walks the Codex section on stub data (--harness codex).
if python3 "$REPORT" --harness codex --dir "$WORK/gate-1" > "$WORK/stub-report.txt" \
    && grep -q "Codex arm (issue #346)" "$WORK/stub-report.txt" \
    && grep -q "specialist engineer round2" "$WORK/stub-report.txt" \
    && grep -q "noise floor arm A: MAD" "$WORK/stub-report.txt"; then
    pass "report prints the Codex section from stub files"
else
    fail "report on stub files"; cat "$WORK/stub-report.txt"
fi
(cd "$WORK/gate-6" && python3 "$REPORT" > "$WORK/all-report.txt") || true
grep -q "Codex arm (issue #346)" "$WORK/all-report.txt" && pass "harness=all includes Codex section" || fail "harness=all lacks Codex section"

# 3. Real path end to end against the fakes. Arm B is a feature-branch
# checkout, so head_ref must record that branch, not GITHUB_REF_NAME=main.
"${G[@]}" checkout -q -b feat-b
HEAD_SHA="$(git -C "$REPO" rev-parse HEAD)"
REAL=(GITHUB_EVENT_NAME=workflow_dispatch REAL_RUN_INPUT=true LOCAL_RUN=true BENCH_HARNESS=codex
      LOCAL_RUN_CONFIG_DIR="$WORK/claude-config" CODEX_AUTH_HOME="$WORK/auth" CODEX_ITERATIONS=2)
reset_calls
OUT="$WORK/real"
RES="$OUT/bench-results/codex"
if run_bench "$OUT" "${REAL[@]}"; then pass "real path (fakes) exits 0"; else fail "real path exit"; cat "$OUT/log.txt"; fi
[ "$(exec_calls)" = 5 ] && pass "2N+1 = 5 operator runs (preflight + 2 per arm)" || fail "expected 5 codex exec calls, got $(exec_calls)"
[ "$(wc -l < "$RES/codex_baseline-scenario.jsonl")" = 2 ] && [ "$(wc -l < "$RES/codex_post-scenario.jsonl")" = 2 ] \
    && pass "N=2 summary records per arm, under bench-results/codex" || fail "record counts"
ls "$OUT"/codex_* > /dev/null 2>&1 && fail "paid-path results written to the workspace root" || pass "no paid-path results in the workspace root"
(cd "$SCRIPT_DIR/.." && git check-ignore -q bench-results/codex/codex_manifest.json) \
    && pass "bench-results/ is gitignored" || fail "bench-results/ not gitignored"
if python3 - "$RES" << 'PY'
import json, sys
for arm in ("baseline", "post"):
    for line in open(f"{sys.argv[1]}/codex_{arm}-scenario.jsonl"):
        r = json.loads(line)
        assert r["operator"]["input_tokens"] == 2100 and r["operator"]["cached_input_tokens"] == 1000, r["operator"]
        roles = r["specialists_by_role_round"]
        assert set(roles) == {"engineer", "security", "review"}, roles
        assert all(set(v) == {"round1"} for v in roles.values()), roles
        assert r["specialist_transcripts_missing"] == 0
PY
then pass "operator + specialist (role, round) accounting"; else fail "summary record contents"; fi
grep -q "Codex arm (issue #346)" "$OUT/log.txt" && pass "report ran at the end" || fail "no report in log"
[ -f "$RES/codex_manifest.json" ] && grep -q '"base_ref": "v1.0.0"' "$RES/codex_manifest.json" \
    && pass "base ref resolved to previous release tag" || fail "manifest/base ref"
grep -q '"head_ref": "feat-b"' "$RES/codex_manifest.json" && grep -q "\"head_sha\": \"$HEAD_SHA\"" "$RES/codex_manifest.json" \
    && pass "manifest records the real head ref and sha" || { fail "manifest head ref"; cat "$RES/codex_manifest.json"; }
grep -q "CODEX_HOME=.*codex-home-a plugin marketplace add .*arm-a" "$CALLS" \
    && grep -q "CODEX_HOME=.*codex-home-b plugin marketplace add $REPO" "$CALLS" \
    && pass "plugin installed per arm from that ref's checkout" || fail "per-ref plugin install"
SCRATCH="$(find "$WORK/tmp" -maxdepth 1 -name 'marc-local-run.*' | head -1)"
ok=1
for arm in a b; do
    h="$SCRATCH/codex-home-$arm/auth.json"
    if [ ! -L "$h" ] || [ "$(readlink "$h")" != "$WORK/auth/auth.json" ]; then ok=0; fi
done
[ "$ok" = 1 ] && pass "auth.json symlinked (not copied) in both CODEX_HOMEs" || fail "auth.json not a symlink"
grep -qx "real=$WORK/bin/claude" "$SCRATCH/run-a-1/shims/claude" && ! grep -q "SHIM" "$SCRATCH/run-a-1/shims/claude" \
    && pass "shim pins the absolute claude path via printf %q" || fail "shim real= line"
grep -rq "$SENTINEL" "$OUT" && fail "credential content leaked into outputs" || pass "credential content never printed"
grep -q "$SENTINEL" "$WORK/auth/auth.json" && pass "real auth.json untouched" || fail "real auth.json modified"
git -C "$REPO" worktree list | grep -q arm-a && fail "arm-A worktree left registered" || pass "arm-A worktree cleaned up"

# 4. Token refresh replaced the symlink -> stop before further spend.
reset_calls
rm -rf "$WORK/tmp"/marc-local-run.*
if run_bench "$WORK/rot" "${REAL[@]}" FAKE_ROTATE=1; then
    fail "rotation should abort"
else
    [ "$(exec_calls)" = 1 ] && grep -q "no longer a symlink" "$WORK/rot/log.txt" \
        && pass "symlink replaced -> aborted after the first run" || fail "rotation abort (exec calls=$(exec_calls))"
fi
grep -q "$SENTINEL" "$WORK/auth/auth.json" && pass "real auth.json still untouched after rotation" || fail "real auth.json modified by rotation"
ROT_SCRATCH="$(find "$WORK/tmp" -maxdepth 1 -name 'marc-local-run.*' | head -1)"
if [ -n "$ROT_SCRATCH" ] && [ -f "$ROT_SCRATCH/codex-home-b/auth.json" ] && [ ! -L "$ROT_SCRATCH/codex-home-b/auth.json" ]; then
    pass "scratch dir and the refreshed token survive the abort"
else
    fail "refreshed token lost on abort (scratch=$ROT_SCRATCH)"
fi
grep -q "mv \"$ROT_SCRATCH/codex-home-b/auth.json\" \"$WORK/auth/auth.json\"" "$WORK/rot/log.txt" \
    && pass "abort message prints the exact recovery command" || { fail "recovery command missing"; cat "$WORK/rot/log.txt"; }

# 5. A failed run (exec #2 = arm A run 1) must not leak its rollouts into
# the next sample. N=3 so the report gate tolerates one discarded sample.
reset_calls
rm -rf "$WORK/tmp"/marc-local-run.*
run_bench "$WORK/failrun" "${REAL[@]}" CODEX_ITERATIONS=3 CODEX_BASE_REF=v1.0.0 FAKE_FAIL_AT=2 || true
FR="$WORK/failrun/bench-results/codex"
FS="$(find "$WORK/tmp" -maxdepth 1 -name 'marc-local-run.*' | head -1)"
grep -q "FAILED: codex exit 1" "$WORK/failrun/log.txt" && pass "failed run reported and discarded" || { fail "no failure logged"; cat "$WORK/failrun/log.txt"; }
[ "$(wc -l < "$FR/codex_baseline-scenario.jsonl")" = 2 ] && pass "arm A kept 2 of 3 samples" || fail "arm A sample count"
if python3 - "$FR" << 'PY'
import json, sys
for arm in ("baseline", "post"):
    for line in open(f"{sys.argv[1]}/codex_{arm}-scenario.jsonl"):
        r = json.loads(line)
        assert r["specialist_invocations"] == 3, r["specialist_invocations"]
        assert "codex-other" not in r["specialists_by_role_round"], r["specialists_by_role_round"]
PY
then pass "next sample's specialist count unchanged after a failed run"; else fail "failed run leaked rollouts into the next sample"; fi
[ -n "$(find "$FS/run-a-1/codex-home-snapshot/sessions" -name 'rollout-*.jsonl' 2>/dev/null)" ] \
    && [ ! -d "$FS/codex-home-a/sessions" ] && pass "failed run's rollouts quarantined under its run dir" || fail "failed run's rollouts not quarantined"

# 6. claude resolving to a relative path fails fast, before any paid call.
reset_calls
rm -rf "$WORK/tmp"/marc-local-run.*
mkdir -p "$WORK/relbin"
cp "$WORK/bin/claude" "$WORK/relbin/claude"
if (cd "$REPO" && env -u GITHUB_OUTPUT GITHUB_WORKSPACE="$WORK/rel" GITHUB_REF_NAME=main PATH="../relbin:$PATH" "${REAL[@]}" bash "$TARGET") > "$WORK/rel.log" 2>&1; then
    fail "relative claude path should abort"
else
    [ "$(exec_calls)" = 0 ] && grep -q "not an absolute path" "$WORK/rel.log" \
        && pass "relative claude path -> fail fast, no codex exec" || { fail "relative claude (exec calls=$(exec_calls))"; cat "$WORK/rel.log"; }
fi

echo
if [ "$FAILS" -ne 0 ]; then
    echo "$FAILS check(s) FAILED"
    exit 1
fi
echo "all Codex-arm checks passed"
