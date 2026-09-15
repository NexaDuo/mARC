#!/usr/bin/env python3
"""Per-task token-benchmark report (issue #275).

Reads the per-task JSONL files scripts/run_token_benchmark.sh writes
(`baseline-<task>.jsonl`, `post-<task>.jsonl`, `toggle_baseline-<task>.jsonl`,
`toggle_post-<task>.jsonl`) for every task name listed in a task-names file
(one name per line, default `task_names.txt` — the single source of truth
run_token_benchmark.sh itself writes, so this report can never silently drift
out of sync with the task set it defines).

Design (see PR body for the full justification):

  * MEDIAN, not sum, across the N iterations within one (task, arm) file.
    Run 34961492007 showed a 2.5x spread across 3 identical-configuration
    runs; summing lets one outlier dominate, median needs a majority of runs
    to agree before it moves.

  * PER-TASK results are printed individually — this is the number that
    should actually be used to judge an optimization, because the three
    tasks are deliberately different workload shapes (a task where the
    guard should lose, one where it plausibly should not, and a neutral
    control where it structurally cannot fire). Collapsing them into one
    figure is exactly the failure mode issue #275 exists to fix.

  * An AGGREGATE (sum of per-task medians) is also printed, because issue
    #275's acceptance criteria ask for both. It is explicitly labeled as
    decoration, not a finding: a single number across dissimilar workload
    shapes answers "what would this look like if you always got a ~1:1:1:1
    mix of these task shapes", which is not a question anyone actually has.
    Read the per-task section for the real result.

  * `fixture` (added issue #293) is INSTRUMENT CALIBRATION, not a product
    claim: a synthetic, generated-at-benchmark-time positive control (see
    scripts/generate_benchmark_fixture.py) built so the guard's target shape
    is guaranteed by construction rather than hoped for, the way `sweep`'s
    is. Its row is explicitly annotated below so a reader skimming only the
    per-task table cannot mistake it for evidence about real work. Like every
    other task here, it never feeds the badge or dashboard (see
    scripts/generate_telemetry_dashboard.py, which is fed `control` alone).
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../core/scripts"))
import token_telemetry_report  # noqa: E402


def median_weighted(path: str) -> tuple[float, int] | None:
    """Return (median weighted tokens, n samples) across every record
    (= one `claude` invocation / iteration) in `path`, or None if the file
    is missing or empty."""
    if not os.path.isfile(path):
        return None
    try:
        records = token_telemetry_report.load_records(path)
    except OSError:
        return None
    weighted = [int(r.get("weighted", 0) or 0) for r in records]
    if not weighted:
        return None
    return statistics.median(weighted), len(weighted)


# Tasks whose number is instrument calibration, not evidence about real
# work (issue #293). Excluded from the badge/dashboard (fed by `control`
# alone) and explicitly flagged in every per-task row here, so a reader
# skimming only the table cannot mistake one for a product claim.
CALIBRATION_TASKS = {"fixture"}


def fmt_cell(sample) -> str:
    if sample is None:
        return "n/a"
    median, n = sample
    return f"{median:,.0f} (n={n})"


def print_comparison_table(title: str, task_names: list[str], base_suffix: str, post_suffix: str, cost_per_million: float, base_dir: str) -> dict[str, float]:
    print(f"--- {title} ---")
    print(f"{'task':<10} {'baseline (median)':>22} {'current (median)':>22} {'delta':>14} {'pct':>8}")
    aggregate_base = 0.0
    aggregate_post = 0.0
    any_data = False
    for name in task_names:
        label = f"{name} [CONTROL]" if name in CALIBRATION_TASKS else name
        base = median_weighted(os.path.join(base_dir, f"{base_suffix}-{name}.jsonl"))
        post = median_weighted(os.path.join(base_dir, f"{post_suffix}-{name}.jsonl"))
        if base is None or post is None:
            print(f"{label:<10} {fmt_cell(base):>22} {fmt_cell(post):>22} {'n/a':>14} {'n/a':>8}")
            continue
        any_data = True
        base_med, post_med = base[0], post[0]
        delta = base_med - post_med
        pct = (delta / base_med * 100) if base_med > 0 else 0.0
        aggregate_base += base_med
        aggregate_post += post_med
        sign = "saved" if delta >= 0 else "cost more"
        print(f"{label:<10} {fmt_cell(base):>22} {fmt_cell(post):>22} {delta:>+14,.0f} {pct:>+7.1f}%  ({sign})")

    if any_data:
        agg_delta = aggregate_base - aggregate_post
        agg_pct = (agg_delta / aggregate_base * 100) if aggregate_base > 0 else 0.0
        print(f"\n[decorative — see per-task rows above for the real result] "
              f"sum of per-task medians: baseline={aggregate_base:,.0f} current={aggregate_post:,.0f} "
              f"delta={agg_delta:+,.0f} ({agg_pct:+.1f}%)")
        cost_diff = (agg_delta / 1_000_000) * cost_per_million
        print(f"(cost delta at ${cost_per_million}/1M weighted tokens, same caveat: ${cost_diff:+.4f})")
    else:
        print("\nno data for either side of this comparison.")
    print()
    return {"aggregate_base": aggregate_base, "aggregate_post": aggregate_post}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=".", help="directory containing the per-task JSONL files (default: cwd)")
    ap.add_argument("--task-names-file", default="task_names.txt", help="file listing task names, one per line (default: task_names.txt)")
    ap.add_argument("--cost-per-million", type=float, default=3.0, help="cost per million weighted tokens (default 3.0)")
    args = ap.parse_args(argv)

    names_path = args.task_names_file if os.path.isabs(args.task_names_file) else os.path.join(args.dir, args.task_names_file)
    if not os.path.isfile(names_path):
        print(f"no task-names file at {names_path} — nothing to report.", file=sys.stderr)
        return 1
    with open(names_path, "r", encoding="utf-8") as fh:
        task_names = [line.strip() for line in fh if line.strip()]
    if not task_names:
        print(f"{names_path} exists but lists no tasks.", file=sys.stderr)
        return 1

    print(f"Task set: {', '.join(task_names)}")
    calibration_present = [n for n in task_names if n in CALIBRATION_TASKS]
    if calibration_present:
        print(f"[CONTROL] = instrument calibration only ({', '.join(calibration_present)}); "
              f"not evidence about real work, never feeds the badge/dashboard.")
    print()

    print_comparison_table(
        "Inter-release Comparison (Previous Release vs Current, guard=350)",
        task_names, "baseline", "post", args.cost_per_million, args.dir,
    )
    print_comparison_table(
        "Causal Proof (No Guard vs Guard=350, same commit)",
        task_names, "toggle_baseline", "toggle_post", args.cost_per_million, args.dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
