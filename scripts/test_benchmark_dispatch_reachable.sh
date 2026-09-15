#!/bin/bash
# Regression test for issue #291.
#
# The real three-arm measurement in run_token_benchmark.sh used to be
# reachable ONLY via `[ "$EVENT_NAME" != "release" ]`, i.e. only on a
# `release` event. That event can never fire in this repo: releases are
# published by release.yml using GITHUB_TOKEN, and GitHub does not raise
# workflow events from GITHUB_TOKEN actions (a documented anti-recursion
# safeguard). So the real path was reachable in source but unreachable in
# practice — the fourth/fifth layer of the same class of bug (#285, #287
# x2, this one).
#
# This test is deliberately NOT pinned to "release is broken" (that
# instance). It asserts the more durable property the issue asks for: the
# real-run path has SOME reachable trigger that does not require cutting a
# release, and a dispatched run that does not explicitly opt in still takes
# the free stub path (so a bare "add a trigger" fix, which would silently
# stub every dispatched run, still fails this test).
#
# Run this against the pre-fix script (`git show origin/main:scripts/run_token_benchmark.sh`)
# and it is RED: `is_real_run` does not exist there at all. Post-fix it is
# GREEN. See the PR body for the actual red/green transcript.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$SCRIPT_DIR/run_token_benchmark.sh}"
WORKFLOW="${2:-$SCRIPT_DIR/../.github/workflows/token-benchmark.yml}"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

# -----------------------------------------------------------------------
# Part 1: the workflow itself must declare a trigger that can run the real
# path without a release (workflow_dispatch, with an explicit opt-in input
# so a casual dispatch doesn't silently spend money).
#
# `@rev` review on PR #292: the original version of this check was
# `grep -q "workflow_dispatch:"` etc. against the raw file text. `@rev`
# demonstrated that a *commented-out* trigger (every line prefixed with
# `#`, so the substrings are still textually present) still passed — a
# green check produced by dead YAML, on the exact path that has already
# shipped four prior "looked wired, wasn't" defects. A backstop a
# commented-out trigger walks through is worse than none.
#
# Fix (round 1): parse the workflow as structured YAML and assert against
# the parsed tree (a `workflow_dispatch:` key literally does not exist in
# the parsed document if it's commented out — there is no text-matching
# hole left).
#
# Fix (round 2, `@rev` review): round 1 kept a "comment-aware" raw-text
# fallback for when PyYAML is unavailable, reasoning its availability on
# the CI runner wasn't guaranteed. `@rev` then broke THAT fallback with a
# more surgical mutation: remove the actual `REAL_RUN_INPUT` wiring from
# the step's `env:` block (the real "declared but never reaches the
# script" regression this whole issue is about), while leaving the literal
# string `REAL_RUN_INPUT` present as a trailing comment on an unrelated
# line. Valid YAML. The PyYAML path (which resolves the wiring through
# actual step/env structure) correctly failed; the bare "does this token
# appear anywhere in the live text" fallback check did not — it wasn't
# scoped to step structure the way the other two fallback checks were.
#
# Decision: DROP the fallback and require PyYAML, failing loudly (not
# silently skipping) if it's absent, rather than patching the fallback to
# be structurally equivalent to the primary path. Two structurally
# different parsers of the same YAML, one of which is weaker in ways
# nobody tracks, is exactly the "looked wired, wasn't" shape this issue
# exists to close — a green result would no longer tell you which check
# actually ran. `@rev` also established the fallback was not dead code
# (nothing in this repo does a bare `import yaml` or pins PyYAML), so
# "keep it as a defensive fallback" was a real, not theoretical, risk.
# Verified before making this call: this repo's Tier 1 CI job runs on
# `ubuntu-latest` with NO `actions/setup-python` step (bare hosted-runner
# python3) and installs no pip packages anywhere in ci.yml today, so
# PyYAML's presence cannot be assumed from the existing setup — ci.yml is
# updated in this same change to `pip install` it explicitly before this
# test runs, so the strong path is guaranteed rather than hoped for.
# -----------------------------------------------------------------------

if [ ! -f "$WORKFLOW" ]; then
    fail "workflow not found at $WORKFLOW"
elif ! python3 -c "import yaml" 2>/dev/null; then
    fail "PyYAML is not available (python3 -c 'import yaml' failed). This test REQUIRES PyYAML to structurally parse token-benchmark.yml -- a raw-text fallback was tried and found foolable (issue #291/PR #292 review: valid YAML that removes real step wiring while leaving the token name present elsewhere as a comment passed a text-based check). Install PyYAML ('pip install pyyaml' / 'python3 -m pip install pyyaml') rather than let this test silently run a weaker check."
else
    if ! python3 - "$WORKFLOW" <<'PYEOF'
import sys

import yaml

path = sys.argv[1]
with open(path) as f:
    text = f.read()

results = []  # (ok: bool, message: str)

def check(ok, msg):
    results.append((ok, msg))

doc = yaml.safe_load(text)
# YAML 1.1 quirk (PyYAML default loader): a bare top-level `on:` key is
# parsed as the boolean True, not the string "on". Check both so this
# doesn't itself become a silent false-negative.
triggers = doc.get("on", doc.get(True))

workflow_dispatch = None
if isinstance(triggers, dict):
    workflow_dispatch = triggers.get("workflow_dispatch")

if isinstance(workflow_dispatch, dict):
    check(True, "token-benchmark.yml declares a workflow_dispatch trigger (parsed YAML)")
else:
    check(False, "token-benchmark.yml has no live workflow_dispatch trigger under 'on:' -- the real path is still reachable only via 'release', which never fires (issue #291)")
    workflow_dispatch = {}

inputs = workflow_dispatch.get("inputs", {}) if isinstance(workflow_dispatch, dict) else {}
if isinstance(inputs, dict) and "real_run" in inputs:
    check(True, "workflow_dispatch declares a 'real_run' input to opt into the paid measurement (parsed YAML)")
else:
    check(False, "workflow_dispatch has no 'real_run' input -- a dispatched run would either always stub (unreachable) or always spend money (unsafe)")

jobs = doc.get("jobs", {}) if isinstance(doc, dict) else {}
steps = []
for job in jobs.values():
    if isinstance(job, dict):
        steps.extend(job.get("steps", []) or [])

def step_run_text(step):
    return step.get("run", "") if isinstance(step, dict) else ""

def step_uses(step):
    return step.get("uses", "") if isinstance(step, dict) else ""

def step_env(step):
    return step.get("env", {}) if isinstance(step, dict) else {}

# Scoped to actual step structure (env keys / run text of each step), NOT a
# bare "does this token appear anywhere in the file" search -- that bare
# form is exactly what `@rev` demonstrated is foolable by a trailing
# comment elsewhere in the file while the real env wiring is removed.
wired = any(
    "REAL_RUN_INPUT" in (step_env(s) or {}) or "REAL_RUN_INPUT" in step_run_text(s)
    for s in steps
)
if wired:
    check(True, "a step wires the dispatch input through to the script (REAL_RUN_INPUT, parsed YAML)")
else:
    check(False, "no step passes the real_run input to run_token_benchmark.sh as REAL_RUN_INPUT -- the input would be declared but never reach the script")

upload_idx = next((i for i, s in enumerate(steps) if "actions/upload-artifact" in step_uses(s)), None)
if upload_idx is not None:
    upload_step = steps[upload_idx]
    if_cond = str(upload_step.get("if", "")).strip()
    if if_cond == "always()":
        check(True, "workflow uploads the raw measurement as an artifact unconditionally (if: always(), parsed YAML)")
    else:
        check(False, f"artifact-upload step exists but its 'if:' is not exactly 'always()' (got: {if_cond!r}) -- a failed publish could destroy a paid-for measurement again")
else:
    check(False, "no step uses actions/upload-artifact -- a failed publish would destroy a paid-for measurement again")

push_idx = next((i for i, s in enumerate(steps) if "git push" in step_run_text(s)), None)
if upload_idx is not None and push_idx is not None and upload_idx < push_idx:
    check(True, "artifact upload step is ordered before the commit/push step (parsed step order)")
else:
    check(False, f"artifact upload step is not ordered before the commit/push step (upload_idx={upload_idx} push_idx={push_idx})")

print("--- workflow structural checks via: PyYAML structural parse ---")
for ok, msg in results:
    print(("ok   - " if ok else "FAIL - ") + msg)

sys.exit(0 if all(ok for ok, _ in results) else 1)
PYEOF
    then
        FAILS=$((FAILS + 1))
    fi
fi

# -----------------------------------------------------------------------
# Part 2: the script's real-vs-stub decision, exercised directly.
# -----------------------------------------------------------------------

if [ ! -f "$TARGET" ]; then
    fail "script not found at $TARGET"
    echo
    echo "$FAILS check(s) FAILED."
    exit 1
fi

# shellcheck source=/dev/null
if ! source "$TARGET" 2>/tmp/test_benchmark_dispatch_reachable.source.err; then
    fail "could not source $TARGET (see /tmp/test_benchmark_dispatch_reachable.source.err)"
    echo
    echo "$FAILS check(s) FAILED."
    exit 1
fi

if ! declare -F is_real_run > /dev/null; then
    fail "is_real_run() is not defined in $TARGET -- the real/stub decision is not an independently testable, reachable trigger (issue #291)"
    echo
    echo "$FAILS check(s) FAILED."
    exit 1
fi

MATRIX_FILE="$(mktemp)"
trap 'rm -f "$MATRIX_FILE"' EXIT

check_real_run() {
    local desc="$1" expected="$2" event="$3" real_run_input="${4:-}"
    local got
    if EVENT_NAME="$event" REAL_RUN_INPUT="$real_run_input" is_real_run; then
        got="true"
    else
        got="false"
    fi
    printf '%s\t%s\t%s\n' "$event" "$real_run_input" "$got" >> "$MATRIX_FILE"
    if [ "$got" = "$expected" ]; then
        pass "$desc (got: $got)"
    else
        fail "$desc -- expected $expected, got $got"
    fi
}

check_real_run "push event stays on the stub path" false "push" ""
check_real_run "pull_request event stays on the stub path" false "pull_request" ""
check_real_run "release event takes the real path" true "release" ""
check_real_run "workflow_dispatch with real_run unset stays on the stub path (safe default)" false "workflow_dispatch" ""
check_real_run "workflow_dispatch with real_run=false stays on the stub path" false "workflow_dispatch" "false"
check_real_run "workflow_dispatch with real_run=true takes the real path -- THE reachable trigger this issue requires" true "workflow_dispatch" "true"

# -----------------------------------------------------------------------
# Part 3: lockstep check between token-benchmark.yml's publish `if:`
# conditions and is_real_run() (issue #296 review, finding 4).
#
# The workflow expresses "is this a real-measurement run" a SECOND time, in
# GitHub Actions expression syntax on the "Generate Dashboard Files" /
# "Commit Dashboard and Benchmarks" steps' `if:` -- independently of
# is_real_run() in run_token_benchmark.sh, with nothing keeping the two in
# sync. This repo has shipped an inert drift guard once before (#281); a
# silent divergence here has two failure directions, both bad: a dispatched
# real run that stops publishing (a paid measurement stranded again, the
# exact #291 failure), or a stub run that starts publishing (fabricated
# data reaching the public badge again, the #274 failure).
#
# This parses BOTH publish steps' `if:` strings as a restricted boolean
# grammar (`&&` / `||` / parens over `github.event_name == 'X'` and
# `github.event.inputs.<name> == 'Y'` comparisons -- the only shape these
# conditions use) and evaluates them against the EXACT SAME
# (event, real_run_input) matrix the check_real_run() calls above just ran
# through is_real_run() directly, reusing their actual results (never a
# second hardcoded "expected" table, which would just be re-asserting this
# script's own opinion against itself). A real divergence -- either publish
# step disagreeing with is_real_run(), or the two publish steps disagreeing
# with each other -- fails loudly. If the grammar ever can't parse a step's
# `if:` (someone rewrites it in a shape this parser doesn't understand),
# this ALSO fails loudly rather than skipping the check silently -- a
# lockstep test that can go quiet on its own subject would be worse than
# none.
# -----------------------------------------------------------------------

echo
echo "--- lockstep check: workflow publish if: vs is_real_run() (parsed YAML) ---"

if ! python3 - "$WORKFLOW" "$MATRIX_FILE" <<'PYEOF'
import re
import sys

import yaml

workflow_path, matrix_path = sys.argv[1], sys.argv[2]

FAILS = 0


def check(ok, msg):
    global FAILS
    print(("ok   - " if ok else "FAIL - ") + msg)
    if not ok:
        FAILS += 1


# --- restricted boolean-expression parser -----------------------------
# Grammar: expr := and_term ('||' and_term)*
#          and_term := atom ('&&' atom)*
#          atom := '(' expr ')' | IDENT '==' STRING
class ParseError(Exception):
    pass


TOKEN_RE = re.compile(r"\s*(\|\||&&|==|\(|\)|'[^']*'|[A-Za-z0-9_.]+)")


def tokenize(text):
    pos = 0
    tokens = []
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ParseError(f"unrecognized token at: {text[pos:]!r}")
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def advance(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse_expr(self):
        node = self.parse_and()
        while self.peek() == "||":
            self.advance()
            node = ("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_atom()
        while self.peek() == "&&":
            self.advance()
            node = ("and", node, self.parse_atom())
        return node

    def parse_atom(self):
        tok = self.peek()
        if tok == "(":
            self.advance()
            node = self.parse_expr()
            if self.advance() != ")":
                raise ParseError("expected closing ')'")
            return node
        ident = self.advance()
        if ident is None or not re.match(r"^[A-Za-z0-9_.]+$", ident):
            raise ParseError(f"expected identifier, got {ident!r}")
        if self.advance() != "==":
            raise ParseError(f"expected '==' after {ident!r}")
        lit = self.advance()
        if lit is None or not (lit.startswith("'") and lit.endswith("'")):
            raise ParseError(f"expected string literal, got {lit!r}")
        return ("eq", ident, lit[1:-1])


def parse_condition(text):
    tokens = tokenize(text)
    parser = Parser(tokens)
    node = parser.parse_expr()
    if parser.i != len(parser.tokens):
        raise ParseError(f"trailing tokens: {parser.tokens[parser.i:]}")
    return node


def eval_node(node, event_name, real_run_input):
    kind = node[0]
    if kind == "or":
        return eval_node(node[1], event_name, real_run_input) or eval_node(node[2], event_name, real_run_input)
    if kind == "and":
        return eval_node(node[1], event_name, real_run_input) and eval_node(node[2], event_name, real_run_input)
    if kind == "eq":
        ident, value = node[1], node[2]
        if ident == "github.event_name":
            return event_name == value
        if ident == "github.event.inputs.real_run":
            return real_run_input == value
        raise ParseError(f"unrecognized identifier in publish if: condition: {ident!r}")
    raise ParseError(f"unrecognized node: {node!r}")


# --- extract the two publish steps' if: from the parsed workflow -------
with open(workflow_path) as f:
    doc = yaml.safe_load(f)

jobs = doc.get("jobs", {}) if isinstance(doc, dict) else {}
steps = []
for job in jobs.values():
    if isinstance(job, dict):
        steps.extend(job.get("steps", []) or [])

PUBLISH_STEP_NAMES = ("Generate Dashboard Files", "Commit Dashboard and Benchmarks")
publish_steps = {s.get("name"): s.get("if") for s in steps if isinstance(s, dict) and s.get("name") in PUBLISH_STEP_NAMES}

if set(publish_steps) != set(PUBLISH_STEP_NAMES):
    check(False, f"could not find both publish steps in {workflow_path} (found: {sorted(publish_steps)})")
    sys.exit(1)

conditions = {name: str(cond) for name, cond in publish_steps.items()}
distinct = set(conditions.values())
if len(distinct) == 1:
    check(True, "'Generate Dashboard Files' and 'Commit Dashboard and Benchmarks' publish under the IDENTICAL if: condition")
else:
    check(False, f"publish steps have DIFFERENT if: conditions -- they can silently disagree about whether to publish: {conditions}")

parsed = {}
for name, cond in conditions.items():
    try:
        parsed[name] = parse_condition(cond)
    except ParseError as exc:
        check(False, f"could not parse '{name}' step's if: condition ({cond!r}) with the restricted boolean grammar: {exc} -- rewrite the condition to the 'github.event_name == ...' / 'github.event.inputs.NAME == ...' shape this lockstep check understands, or extend the parser (never skip the check)")

if len(parsed) != len(PUBLISH_STEP_NAMES):
    sys.exit(1 if FAILS else 0)

with open(matrix_path) as f:
    rows = [line.rstrip("\n").split("\t") for line in f if line.strip()]

mismatches = 0
for event_name, real_run_input, is_real_run_result in rows:
    expected = is_real_run_result == "true"
    for name, node in parsed.items():
        try:
            got = eval_node(node, event_name, real_run_input)
        except ParseError as exc:
            check(False, f"'{name}' if: condition references something this parser doesn't recognize: {exc}")
            mismatches += 1
            continue
        if got != expected:
            check(False, f"'{name}' if: condition disagrees with is_real_run() for event={event_name!r} real_run_input={real_run_input!r}: "
                          f"if: says {got}, is_real_run() says {expected}")
            mismatches += 1

if mismatches == 0 and FAILS == 0:
    check(True, f"both publish if: conditions agree with is_real_run() across all {len(rows)} matrix cases")

sys.exit(1 if FAILS else 0)
PYEOF
then
    FAILS=$((FAILS + 1))
fi

echo
if [ "$FAILS" -eq 0 ]; then
    echo "All checks passed."
    exit 0
else
    echo "$FAILS check(s) FAILED."
    exit 1
fi
