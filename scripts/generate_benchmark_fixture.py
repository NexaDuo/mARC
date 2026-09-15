#!/usr/bin/env python3
"""Deterministic fixture generator for the `fixture` benchmark task (issue #293
review, `@rev` finding: the `sweep` task's guard-favorable shape is an
unverified bet on this repo's actual file sizes, and the agent may reach for
`Grep` — which the read-guard never intercepts — regardless of arm).

This generates a SYNTHETIC POSITIVE CONTROL: a small fixture where the guard's
target shape (many files comfortably over the 350-line threshold) is
GUARANTEED by construction rather than hoped for, and where the correct
answer is a single verifiable integer, so a wrong answer is detectable.

Why a generator, not committed fixture data: the fixture content itself
(thousands of lines across several files) is disposable, exists only for the
duration of one benchmark run, and would otherwise be pure repo-size bloat
with no reason for a human ever to read or diff it. Keeping only this ~150
line generator in git (and regenerating byte-identical content at benchmark
time) is far cheaper to review and maintain than committing the bulk output.

Design so a full read is the natural unguarded strategy (not a `grep`-collapsible
pattern): every line embeds exactly one "signal" count, spelled out in English
words ("seventeen", not "17"), inside a sentence that ALSO contains two decoy
digits (a module id and a pass id) that are NOT part of the sum. A lazy
`grep -oE '[0-9]+' | paste -sd+ | bc`-style digit sum is not just weaker than
reading — it is WRONG, because it would include the decoy digits. Sentence
templates additionally rotate (4 variants), so no single fixed regex position
reliably isolates the signal word across every line. This does not make a
clever extraction script impossible — see the caveat in the PR body — but it
removes the single-`grep`-collapses-it failure mode `@rev` flagged for `sweep`.

Determinism: every value below is a pure function of (file index, line
index) — no `random` module, no timestamps, no filesystem state. Calling
`build_fixture()` twice with the same arguments always yields byte-identical
content, so re-running the generator across arm A / B / C in the same
benchmark invocation (or in CI) never introduces its own variance.
"""
from __future__ import annotations

import argparse
import json
import os

# Six files: five comfortably over the 350-line read-guard threshold (420
# lines each), one well under it (150 lines) — a clear majority (5/6) above
# the threshold, by construction rather than by hoping the repo happens to be
# shaped that way (contrast with `sweep`, which found only 3/10 files
# qualify). The one small file is intentional: it keeps the fixture from
# being uniformly one-shape and mirrors the "not every file is huge" texture
# of a real repo, without threatening the "clear majority" requirement.
DEFAULT_FILE_SIZES = [420, 420, 420, 420, 420, 150]

_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_TEENS = [
    "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty"]

_NOUNS = ["incidents", "anomalies", "alerts", "regressions", "warnings", "retries"]

_TEMPLATES = [
    "Cycle {i}: module {f} logged {word} {noun} before the deploy window closed.",
    "During pass {i}, {word} {noun} were attributed to module {f} by the on-call engineer.",
    "Module {f} (pass {i}) -- the {noun} tally came in at {word} after triage.",
    "{word_cap} {noun} surfaced from module {f} during review cycle {i}, per the pass-{i} audit.",
]


def num_to_words(n: int) -> str:
    """1..59 inclusive -> English words. Pure, deterministic."""
    if not 1 <= n <= 59:
        raise ValueError(f"num_to_words only supports 1..59, got {n}")
    if n < 10:
        return _ONES[n]
    if n < 20:
        return _TEENS[n - 10]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def signal_value(file_idx: int, line_idx: int) -> int:
    """Deterministic 1..59 value for a given (file, line). `line_idx` is
    1-based (matches the sentence's own "cycle"/"pass" numbering)."""
    return ((file_idx * 131 + line_idx * 17 + 7) % 59) + 1


def build_fixture(file_sizes: list[int] = None) -> tuple[dict[str, str], int, list[int]]:
    """Pure function, no side effects. Returns (filename -> content, total
    sum of every signal value across every file, per-file subtotal list) for
    `file_sizes` (default DEFAULT_FILE_SIZES; index i has file_sizes[i] lines).
    """
    file_sizes = DEFAULT_FILE_SIZES if file_sizes is None else file_sizes
    files: dict[str, str] = {}
    per_file_totals: list[int] = []
    total = 0
    for f, size in enumerate(file_sizes):
        lines = []
        subtotal = 0
        for i in range(1, size + 1):
            value = signal_value(f, i)
            subtotal += value
            word = num_to_words(value)
            noun = _NOUNS[(f * 3 + i * 5) % len(_NOUNS)]
            template = _TEMPLATES[(f + i) % len(_TEMPLATES)]
            lines.append(template.format(i=i, f=f, word=word, word_cap=word.capitalize(), noun=noun))
        files[f"fixture_{f:02d}.txt"] = "\n".join(lines) + "\n"
        per_file_totals.append(subtotal)
        total += subtotal
    return files, total, per_file_totals


def write_fixture(out_dir: str, expected_file: str | None = None, file_sizes: list[int] = None) -> int:
    """Writes the fixture files to `out_dir` and (if `expected_file` given)
    the ground-truth total + per-file breakdown to `expected_file`. Returns
    the total. `expected_file` is deliberately NOT placed inside `out_dir`:
    the benchmark task tells the agent to read every file in `out_dir`, so
    the answer key must live outside it to avoid contaminating the task.
    """
    os.makedirs(out_dir, exist_ok=True)
    files, total, per_file_totals = build_fixture(file_sizes)
    for name, content in files.items():
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write(content)
    if expected_file:
        with open(expected_file, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "total": total,
                    "per_file_totals": per_file_totals,
                    "file_sizes": file_sizes or DEFAULT_FILE_SIZES,
                    "file_names": sorted(files.keys()),
                },
                fh,
                indent=2,
            )
            fh.write("\n")
    return total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", help="directory to write fixture_NN.txt files into (created if missing)")
    ap.add_argument(
        "--expected-file",
        default=None,
        help="path to write the ground-truth total/breakdown JSON (default: <out_dir>.expected.json, a SIBLING of out_dir, never inside it)",
    )
    args = ap.parse_args(argv)

    expected_file = args.expected_file or (args.out_dir.rstrip("/") + ".expected.json")
    total = write_fixture(args.out_dir, expected_file=expected_file)
    print(f"Wrote fixture to {args.out_dir} ({len(DEFAULT_FILE_SIZES)} files). Ground-truth total: {total} (recorded in {expected_file})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
