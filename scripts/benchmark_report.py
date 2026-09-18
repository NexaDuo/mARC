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
    decoration, not a finding: a single number across three dissimilar
    workload shapes answers "what would this look like if you always got a
    ~1:1:1 mix of these three task shapes", which is not a question anyone
    actually has. Read the per-task section for the real result.
"""
from __future__ import annotations

import argparse
import hashlib
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


def pooled_weighted(paths: list[str]) -> list[int]:
    """Pool every "weighted" value across `paths`, de-duplicating any path
    whose full set of values is identical to one already pooled.

    Issue #308: `scripts/run_token_benchmark.sh` deliberately reuses
    `post-<task>.jsonl` as `toggle_post-<task>.jsonl` (arm B is the guard-on
    side of both the inter-release comparison AND the same-commit "causal
    proof" toggle comparison, an intentional cost saving -- one fewer set of
    paid `claude` invocations per release). Those two files are
    byte-identical for every task in the archived run. Pooling "4 files" as
    if they were 4 independent samples double-counts the same 5
    measurements as 10, understating the true dispersion. This pools by
    DISTINCT measured arm: a file whose value tuple has already been seen
    contributes nothing further.
    """
    seen: set[str] = set()
    pooled: list[int] = []
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        try:
            records = token_telemetry_report.load_records(path)
        except OSError:
            continue
        values = tuple(int(r.get("weighted", 0) or 0) for r in records)
        if not values:
            continue
        digest = hashlib.sha256(repr(values).encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        pooled.extend(values)
    return pooled


class MadFloor:
    """A dispersion-based noise floor (issue #308).

    `mad_abs`   -- median absolute deviation, in weighted tokens.
    `mad_pct`   -- mad_abs expressed as a percentage of the pooled median.
    `n`         -- count of pooled, de-duplicated observations the estimator
                   was computed over (kept small and explicit -- issue #298
                   remains open on whether n this small can be trusted for
                   the floor's numeric VALUE; this class only fixes the
                   estimator and its presentation, not that question).
    """

    __slots__ = ("mad_abs", "mad_pct", "n")

    def __init__(self, mad_abs: float, mad_pct: float, n: int) -> None:
        self.mad_abs = mad_abs
        self.mad_pct = mad_pct
        self.n = n


def mad_floor(paths: list[str]) -> MadFloor | None:
    """Compute a MAD-based noise floor over the pooled, de-duplicated
    "weighted" observations found in `paths`.

    Replaces the previous "gap between two medians" floor (median-vs-median
    is a claim about central tendency, not dispersion -- over the same five
    `toggle_baseline-neutral` observations, median-vs-median put the floor
    at 33.7% while MAD puts it at 2.1%; pooled across both same-commit arms
    per this function's de-duplication, MAD is ~31.5% on the archived run).
    Returns None if fewer than 2 distinct observations are available (no
    dispersion is computable) or the pooled median is <= 0.
    """
    values = pooled_weighted(paths)
    if len(values) < 2:
        return None
    center = statistics.median(values)
    if center <= 0:
        return None
    mad_abs = statistics.median([abs(v - center) for v in values])
    mad_pct = (mad_abs / center) * 100
    return MadFloor(mad_abs, mad_pct, len(values))


def fmt_cell(sample, iterations: int | None = None) -> str:
    if sample is None:
        return "n/a" if iterations is None else f"n/a (n=0/{iterations})"
    median, n = sample
    if iterations is None:
        return f"{median:,.0f} (n={n})"
    return f"{median:,.0f} (n={n}/{iterations})"


# Issue #295, AC3: the proposed threshold is literally "3 of 5" (this repo's
# current ITERATIONS=5). Generalized as a 3/5 ratio of whatever ITERATIONS
# this run actually used (floor), rather than hardcoding "3", so the gate
# keeps working if ITERATIONS is ever changed elsewhere -- this PR does not
# change ITERATIONS itself.
UNTRUSTWORTHY_RATIO_NUM = 3
UNTRUSTWORTHY_RATIO_DEN = 5

# ARM_D_TASK (issue #324): must match ARM_D_TASK in
# scripts/run_token_benchmark.sh exactly. Arm D (bulk_reader=true) makes
# real, billed `claude` invocations through the worker delegation path
# (#320/#322), so run_token_benchmark.sh deliberately runs it against only
# this one task -- the task verified to deterministically trip the guard's
# enforcement path (see that script's task-set comment). This report
# compares arm B (`post-<task>.jsonl`, enforcement only) against arm D
# (`bulk_reader-<task>.jsonl`, enforcement + execution) for this task ONLY;
# other tasks never have a `bulk_reader-<task>.jsonl` file and are not
# expected to.
ARM_D_TASK = "bulk_read_forced"


def untrustworthy_threshold(iterations: int) -> int:
    return (iterations * UNTRUSTWORTHY_RATIO_NUM) // UNTRUSTWORTHY_RATIO_DEN


def cell_status(sample, iterations: int) -> str:
    """Classify one (task, arm, file) cell against the requested iteration
    count. Issue #295 AC2/AC3: any cell below ITERATIONS is flagged; a cell
    at or below the untrustworthy threshold (3/5 by default) marks the row
    untrustworthy rather than silently reporting a median over a
    silently-reduced n."""
    n = 0 if sample is None else sample[1]
    if n >= iterations:
        return "ok"
    if n <= untrustworthy_threshold(iterations):
        return "UNTRUSTWORTHY"
    return "SHORT"


def _worse(a: str, b: str) -> str:
    rank = {"ok": 0, "SHORT": 1, "UNTRUSTWORTHY": 2}
    return a if rank[a] >= rank[b] else b


def print_comparison_table(title: str, task_names: list[str], base_suffix: str, post_suffix: str, cost_per_million: float, base_dir: str, iterations: int | None = None) -> dict:
    print(f"--- {title} ---")
    header = f"{'task':<10} {'baseline (median)':>22} {'current (median)':>22} {'delta':>14} {'pct':>8}"
    if iterations is not None:
        header += f"  {'status':<13}"
    print(header)
    aggregate_base = 0.0
    aggregate_post = 0.0
    any_data = False
    has_untrustworthy = False
    for name in task_names:
        base = median_weighted(os.path.join(base_dir, f"{base_suffix}-{name}.jsonl"))
        post = median_weighted(os.path.join(base_dir, f"{post_suffix}-{name}.jsonl"))

        status_suffix = ""
        if iterations is not None:
            status = _worse(cell_status(base, iterations), cell_status(post, iterations))
            if status != "ok":
                has_untrustworthy = has_untrustworthy or status == "UNTRUSTWORTHY"
                status_suffix = f"  {status:<13}"
            else:
                status_suffix = f"  {'ok':<13}"

        if base is None or post is None:
            print(f"{name:<10} {fmt_cell(base, iterations):>22} {fmt_cell(post, iterations):>22} {'n/a':>14} {'n/a':>8}{status_suffix}")
            continue
        any_data = True
        base_med, post_med = base[0], post[0]
        delta = base_med - post_med
        pct = (delta / base_med * 100) if base_med > 0 else 0.0
        aggregate_base += base_med
        aggregate_post += post_med
        sign = "saved" if delta >= 0 else "cost more"
        print(f"{name:<10} {fmt_cell(base, iterations):>22} {fmt_cell(post, iterations):>22} {delta:>+14,.0f} {pct:>+7.1f}%  ({sign}){status_suffix}")

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
    if iterations is not None and has_untrustworthy:
        print(f"\n⚠ UNTRUSTWORTHY: at least one cell above has n <= {untrustworthy_threshold(iterations)}/{iterations} "
              f"samples (issue #295) -- its median is over a silently-reduced n and should not be trusted.")
    print()
    return {"aggregate_base": aggregate_base, "aggregate_post": aggregate_post, "has_untrustworthy": has_untrustworthy}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=".", help="directory containing the per-task JSONL files (default: cwd)")
    ap.add_argument("--task-names-file", default="task_names.txt", help="file listing task names, one per line (default: task_names.txt)")
    ap.add_argument("--cost-per-million", type=float, default=3.0, help="cost per million weighted tokens (default 3.0)")
    ap.add_argument("--iterations", type=int, default=None,
                     help="expected sample count per (task, arm) cell (issue #295). When given, each cell is "
                          "compared against this count and flagged SHORT/UNTRUSTWORTHY; if any cell is "
                          "UNTRUSTWORTHY (n <= 3/5 of --iterations) this script exits non-zero rather than "
                          "silently reporting a median over a silently-reduced n. Omit for the free stub path, "
                          "whose fixed n=2 samples are not a real measurement and must not trip this gate.")
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

    print(f"Task set: {', '.join(task_names)}\n")

    result_a = print_comparison_table(
        "Inter-release Comparison (Previous Release vs Current, guard=350)",
        task_names, "baseline", "post", args.cost_per_million, args.dir,
        iterations=args.iterations,
    )
    result_b = print_comparison_table(
        "Causal Proof (No Guard vs Guard=350, same commit)",
        task_names, "toggle_baseline", "toggle_post", args.cost_per_million, args.dir,
        iterations=args.iterations,
    )

    # Issue #308 review (`@rev`, PR #310): the same-commit toggle pair's
    # pooled dispersion is only a NOISE floor for `neutral` -- the task the
    # guard structurally cannot fire on in either arm, so its two arms are
    # draws from the same distribution. The guard DOES fire on `control`
    # and `sweep` (issue #308's own Finding 2: `control`'s old-estimator
    # 159.9% was real guard effect, not noise), so pooling their two arms
    # mixes signal into a number that would be mislabeled "noise" -- a
    # reader could mistake it for a legitimate per-task floor and gate
    # something on it. Only ever compute/print this for `neutral`; do not
    # extend this loop to other tasks without re-deriving whether their two
    # same-commit arms are actually a negative control.
    NOISE_FLOOR_TASK = "neutral"
    print(f"--- Noise Floor (MAD, same-commit toggle pair, {NOISE_FLOOR_TASK} only) ---")
    print(f"({NOISE_FLOOR_TASK} is the only task the guard structurally cannot fire on in "
          f"either arm, so it is the only valid noise-floor source; other tasks' same-commit "
          f"dispersion includes real guard effect and is not printed here to avoid mislabeling "
          f"signal as noise)")
    if NOISE_FLOOR_TASK in task_names:
        print(f"{'task':<10} {'MAD (abs)':>14} {'MAD (pct)':>10} {'n':>4}")
        floor = mad_floor([
            os.path.join(args.dir, f"toggle_baseline-{NOISE_FLOOR_TASK}.jsonl"),
            os.path.join(args.dir, f"toggle_post-{NOISE_FLOOR_TASK}.jsonl"),
        ])
        if floor is None:
            print(f"{NOISE_FLOOR_TASK:<10} {'n/a':>14} {'n/a':>10} {'0':>4}")
        else:
            print(f"{NOISE_FLOOR_TASK:<10} {floor.mad_abs:>14,.0f} {floor.mad_pct:>9.1f}% {floor.n:>4}")
    else:
        print(f"(no '{NOISE_FLOOR_TASK}' task in this task set -- no noise floor to report)")
    print("(provisional -- issue #298: n this small is too few to trust the floor's numeric "
          "value on its own; this is the estimator/presentation fix, not a trustworthiness claim)\n")

    # Issue #324: the meaningful contrast for the study's claim is arm B
    # (enforcement only, the guard denies) vs arm D (enforcement +
    # execution, the bulk-reader worker summarizes instead) -- NOT arm D
    # against the guard-off control (arm C, printed above as the "Causal
    # Proof" pair for a different purpose). Comparing D against C would
    # just repeat the enforcement-on-vs-enforcement-off comparison that
    # already produced the known-negative result (run 34987534132: guard
    # costs 2.6x, noise floor 33.7%) and would say nothing about whether
    # delegating instead of denying actually helps. Printed only for
    # ARM_D_TASK -- the one task run_token_benchmark.sh actually runs arm D
    # against; every other task legitimately has no `bulk_reader-<task>.jsonl`
    # file and is not a report bug.
    #
    # This table does NOT feed the "Tokens Saved" badge (generate_badge() in
    # scripts/generate_telemetry_dashboard.py) -- the badge is fed the
    # SHIPPED-DEFAULT arm (#303/#305), and `bulk_reader` defaults to OFF, so
    # this comparison is reported here, in the console/job-summary report,
    # and nowhere else.
    result_d = None
    if ARM_D_TASK in task_names:
        result_d = print_comparison_table(
            f"Execution Layer Proof (Arm B: enforcement-only/deny vs Arm D: "
            f"enforcement+execution/bulk_reader=true, same commit, '{ARM_D_TASK}' only) -- issue #324",
            [ARM_D_TASK], "post", "bulk_reader", args.cost_per_million, args.dir,
            iterations=args.iterations,
        )
    else:
        print(f"(no '{ARM_D_TASK}' task in this task set -- no execution-layer comparison to report)\n")

    has_untrustworthy = result_a["has_untrustworthy"] or result_b["has_untrustworthy"]
    if result_d is not None:
        # Arm D's worker invocations are real, billed `claude` calls
        # (issue #324) -- a worker failure/instrument-loss on that cell must
        # gate the run exactly the same way arm A/B/C already do, not
        # silently bias the cell by reporting a median over a
        # silently-reduced n.
        has_untrustworthy = has_untrustworthy or result_d["has_untrustworthy"]

    if args.iterations is not None and has_untrustworthy:
        print(f"FAIL: at least one (task, arm) cell has n <= {untrustworthy_threshold(args.iterations)}/{args.iterations} "
              "samples (issue #295). Failing loudly instead of publishing a median over a silently-reduced n.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
