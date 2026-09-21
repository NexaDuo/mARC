#!/usr/bin/env python3
"""Rule-origin governance gate (issue #68).

Durable rules in the mARC plugin (agent Non-negotiables, tech-lead Principles,
dispatch cost-discipline rules) must carry an origin tag `(origin: #NN · DATE)`
so every rule's provenance stays auditable. The governed regions are fenced with

    <!-- rules:origin-required -->
    ... rules ...
    <!-- /rules:origin-required -->

This script scans the fenced regions in the files passed as arguments and fails
if any rule inside a fence lacks an origin tag (or if a fence is left unclosed).

A "rule" is a block that starts with a Markdown bold lead — either a list bullet
`- **...**` or a bold-lead paragraph `**...**` at the start of a line — and runs
until the next such lead or the closing fence. Stdlib only; deterministic; zero
token cost. Run it with the real files (POSITIVE) and a synthetic missing-tag
fixture (NEGATIVE) — see the CI step that drives it.
"""
import re
import sys

OPEN = "<!-- rules:origin-required -->"
CLOSE = "<!-- /rules:origin-required -->"

# A rule starts with a bold lead at column 0: an optional list marker then `**`.
# Leading whitespace is deliberately NOT allowed — continuation lines are indented
# and may legitimately begin with inline bold (e.g. "  **NOT** find …"), so anchoring
# at column 0 keeps a wrapped rule from being mis-split into a new (untagged) rule.
RULE_START = re.compile(r"^(?:[-*] )?\*\*")
# Origin tag: `(origin: #<digits> · YYYY-MM-DD)`. The separator is a middot
# (U+00B7); the date is ISO. Whitespace around the middot is tolerated.
ORIGIN = re.compile(r"\(origin:\s*#\d+\s*·\s*\d{4}-\d{2}-\d{2}\s*\)")


def check_file(path):
    """Return a list of human-readable failure strings for one file."""
    failures = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError as e:
        return [f"{path}: cannot read — {e}"]

    in_region = False
    region_open_line = 0
    # Accumulate the current rule block: (start_line_no, [text...]).
    rule_start = None
    rule_text = []

    def flush_rule():
        if rule_start is None:
            return
        block = "\n".join(rule_text)
        if not ORIGIN.search(block):
            head = rule_text[0].strip()
            head = (head[:80] + "…") if len(head) > 80 else head
            failures.append(
                f"{path}:{rule_start}: rule in origin-required region lacks an "
                f"'(origin: #NN · YYYY-MM-DD)' tag → {head!r}"
            )

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped == OPEN:
            if in_region:
                failures.append(
                    f"{path}:{i}: nested/duplicate opening fence "
                    f"(already open since line {region_open_line})"
                )
            in_region = True
            region_open_line = i
            rule_start, rule_text = None, []
            continue
        if stripped == CLOSE:
            if not in_region:
                failures.append(f"{path}:{i}: closing fence with no open fence")
            else:
                flush_rule()
            in_region = False
            rule_start, rule_text = None, []
            continue
        if not in_region:
            continue
        # Inside a governed region.
        if RULE_START.match(line):
            flush_rule()
            rule_start, rule_text = i, [line]
        elif rule_start is not None:
            rule_text.append(line)
        # else: leading prose inside the region before the first rule — ignore.

    if in_region:
        failures.append(
            f"{path}:{region_open_line}: opening fence is never closed "
            f"(missing '{CLOSE}')"
        )
    return failures


def main(argv):
    files = argv[1:]
    if not files:
        print("usage: check_rule_origin.py <file.md> [file.md ...]", file=sys.stderr)
        return 2
    all_failures = []
    for path in files:
        all_failures.extend(check_file(path))
    if all_failures:
        for f in all_failures:
            print(f"::error::{f}")
        print(f"\nrule-origin gate: FAILED — {len(all_failures)} issue(s).")
        return 1
    print(f"rule-origin gate: OK — every governed rule in {len(files)} file(s) "
          f"carries an '(origin: #NN · YYYY-MM-DD)' tag.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
