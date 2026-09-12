#!/bin/bash
# Regression test for issue #274 (reopened, `@rev` finding on PR #284).
#
# .github/workflows/token-benchmark.yml used to post a PR comment
# ("### Token Consumption Benchmark Report", including a line like
# "Percentage saved: 15.2%") on every `pull_request` (and this workflow has
# no `release`-only PR-comment path — pull_request events never carry
# github.event_name == 'release'), computed from the stub
# baseline.jsonl/post.jsonl that scripts/run_token_benchmark.sh's
# non-release branch always writes. That comment reads to a human as an
# announced measurement even though the input was synthetic, which is the
# exact fabrication issue #274 is about — this time on the PR-comment
# channel instead of the docs channel.
#
# Per #274's acceptance criteria ("confine stub output to the job
# summary"), no step in this workflow may post a benchmark report to a
# public PR/issue comment unless it is gated to `github.event_name ==
# 'release'` (the only event where the underlying data can be a real
# measurement instead of a stub).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="${1:-$REPO_ROOT/.github/workflows/token-benchmark.yml}"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

if [ ! -f "$WORKFLOW" ]; then
    fail "workflow not found at $WORKFLOW"
    exit 1
fi

# Deliberately avoids a YAML-parsing dependency (PyYAML availability on the
# runner is not guaranteed for the bare system python3 used elsewhere in
# this CI job): steps are found by splitting the workflow text on each
# "- name:" step marker (any indent — this workflow's steps sit at 6
# spaces under jobs.<job>.steps), and each step's own text (up to the next
# step marker) is checked for an un-gated external-comment call.
set +e
python3 - "$WORKFLOW" <<'PYEOF'
import re
import sys

path = sys.argv[1]
with open(path) as f:
    text = f.read()

# Split into per-step chunks on the "- name:" step boundary (a YAML list
# item, indentation-agnostic), keeping each step's own text.
step_starts = [m.start() for m in re.finditer(r"^\s*- name:", text, re.MULTILINE)]
step_starts.append(len(text))
steps = []
for i in range(len(step_starts) - 1):
    chunk = text[step_starts[i]:step_starts[i + 1]]
    name_match = re.match(r"^\s*- name:\s*(.+)$", chunk, re.MULTILINE)
    name = name_match.group(1).strip() if name_match else "<unnamed>"
    steps.append((name, chunk))

fails = []
comment_pattern = re.compile(r"gh pr comment|gh issue comment")

for name, chunk in steps:
    if not comment_pattern.search(chunk):
        continue

    if_match = re.search(r"^\s*if:\s*(.+)$", chunk, re.MULTILINE)
    cond = if_match.group(1).strip() if if_match else ""
    is_release_gated = "release" in cond and "event_name" in cond
    if not is_release_gated:
        fails.append(
            f"step '{name}' posts an external comment (gh pr comment / gh "
            f"issue comment) but is not gated to a release-only 'if' "
            f"condition (found if: {cond!r}). This can publish "
            f"stub-derived numbers as if they were measured."
        )

if fails:
    for msg in fails:
        print("FAIL -", msg)
    sys.exit(1)
else:
    print("ok   - no un-gated external-comment step found in token-benchmark.yml")
    sys.exit(0)
PYEOF
status=$?
set -e

if [ "$status" -ne 0 ]; then
    fail "token-benchmark.yml has an un-gated PR/issue comment step (see above)"
else
    pass "token-benchmark.yml has no un-gated PR/issue comment step"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then
    echo "All token-benchmark comment-channel checks passed."
    exit 0
else
    echo "$FAILS check(s) failed."
    exit 1
fi
