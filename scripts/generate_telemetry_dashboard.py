#!/usr/bin/env python3
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../core/scripts'))
import token_telemetry_report

# Sibling script, same directory as this file (scripts/) — no sys.path
# manipulation needed. Reused deliberately (issue #296): benchmark_report.py
# already computes MEDIAN weighted tokens per (task, arm) file, with the
# reasoning documented in its module docstring (run 34961492007 showed a
# 2.5x spread across 3 identical-configuration runs; a sum lets one outlier
# dominate). The badge previously summed instead, i.e. it used a rejected
# methodology over the same data (PR #293) — import and call the one real
# implementation instead of re-deriving a second median calculation here.
import benchmark_report

def generate_markdown(sessions, output_path):
    # Sort sessions by timestamp
    ordered = sorted(
        sessions.items(),
        key=lambda kv: max((t.get("ts", 0) or 0) for t in kv[1])
    )
    
    x_axis = []
    y_axis = []
    for sid, turns in ordered:
        totals = token_telemetry_report.session_totals(turns)
        x_axis.append(sid[:8])
        y_axis.append(totals["weighted"])
        
    md = [
        "# Token Telemetry Dashboard",
        "",
        "## Token Consumption Trend",
        "",
        "```mermaid",
        "xychart-beta",
        f"    title \"Weighted Tokens per Session\"",
        f"    x-axis [{', '.join(x_axis)}]",
        f"    y-axis \"Tokens\"",
        f"    line [{', '.join(map(str, y_axis))}]",
        "```",
        ""
    ]
    
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(md))

BADGE_LABEL = "Tokens Saved (Last Release)"


def _pct_delta(base_median: float, post_median: float) -> float:
    return ((base_median - post_median) / base_median) * 100


def generate_badge(baseline_path, current_path, neutral_baseline_path, neutral_current_path, output_path):
    """Write the "Tokens Saved (Last Release)" shields.io endpoint badge.

    Unconditional by design (origin: #285): this is always called from
    main(), in the same invocation/run the markdown dashboard was just
    written from, so the badge can never be left stale while the dashboard
    moves on.

    Issue #296 re-scope: the badge answers "tokens saved on THIS release",
    not a standing "mARC saves X%" property — 0/"No Change" is the correct,
    expected reading for most releases. Three states, in order:

      1. "No Data" (inactive) — no baseline/current data, or no `neutral`
         data to measure a noise floor from. A missing noise floor falls
         back to "No Data", NEVER to an ungated raw number — publishing a
         delta without a floor to gate it is the exact failure that put a
         fabricated 15.2% on this badge before (issue #274/#279).
      2. "No Change" (informational) — the reported task's delta does not
         exceed the noise floor measured from `neutral` (the task the guard
         structurally cannot fire on in either arm; its own baseline-vs-post
         gap is pure run-to-run variance — run 34987534132 measured it at
         33.7%). Below that floor, a number is indistinguishable from noise.
      3. A real percentage (success/orange) — the delta clears the floor.

    MEDIAN, not sum, across iterations (reusing benchmark_report.
    median_weighted — see its module docstring and the import comment above
    for why): the badge and scripts/benchmark_report.py must use the same
    methodology over the same data, never two competing ones (issue #296).
    """
    no_data_badge = {
        "schemaVersion": 1,
        "label": BADGE_LABEL,
        "message": "No Data",
        "color": "inactive",
    }

    def write(badge):
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(badge, f, indent=2)

    if not baseline_path or not current_path:
        write(no_data_badge)
        return

    baseline_sample = benchmark_report.median_weighted(baseline_path)
    current_sample = benchmark_report.median_weighted(current_path)
    if baseline_sample is None or current_sample is None:
        write(no_data_badge)
        return
    base_median, _ = baseline_sample
    post_median, _ = current_sample
    if base_median <= 0:
        # A baseline file was provided but carries no measured tokens —
        # still honest "No Data", not a divide-by-zero 0.0%.
        write(no_data_badge)
        return

    # Noise floor: the absolute percentage gap between `neutral`'s own two
    # arms. `neutral` cannot be affected by the thing under test, so this
    # gap IS the instrument's measurement noise, computed fresh from this
    # run's own data — never hardcoded (issue #296).
    floor_pct = None
    if neutral_baseline_path and neutral_current_path:
        neutral_base_sample = benchmark_report.median_weighted(neutral_baseline_path)
        neutral_post_sample = benchmark_report.median_weighted(neutral_current_path)
        if neutral_base_sample is not None and neutral_post_sample is not None and neutral_base_sample[0] > 0:
            floor_pct = abs(_pct_delta(neutral_base_sample[0], neutral_post_sample[0]))

    if floor_pct is None:
        # `neutral` is missing or yields no usable floor — publish nothing
        # rather than an ungated number (see the docstring above).
        write(no_data_badge)
        return

    pct = _pct_delta(base_median, post_median)
    if abs(pct) <= floor_pct:
        badge = {
            "schemaVersion": 1,
            "label": BADGE_LABEL,
            "message": "No Change",
            "color": "informational",
        }
    else:
        badge = {
            "schemaVersion": 1,
            "label": BADGE_LABEL,
            "message": f"{pct:.1f}%",
            "color": "success" if pct > 0 else "orange",
        }
    write(badge)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True,
                         help="current/post-optimization telemetry JSONL (drives the markdown trend chart)")
    parser.add_argument("--baseline", required=False,
                         help="baseline (pre-optimization / previous-release) telemetry JSONL to diff "
                              "against --path for the 'Tokens Saved (Last Release)' badge; omit for an "
                              "honest 'No Data' badge")
    parser.add_argument("--neutral-baseline", required=False,
                         help="baseline telemetry JSONL for the 'neutral' task (the task the guard "
                              "structurally cannot fire on) — its own baseline-vs-post gap is the "
                              "measurement noise floor the badge gates on. Omit for an honest 'No Data' "
                              "badge rather than an ungated number.")
    parser.add_argument("--neutral-path", required=False,
                         help="current/post telemetry JSONL for the 'neutral' task, paired with "
                              "--neutral-baseline for the noise floor.")
    parser.add_argument("--md-out", default="docs/marc/telemetry.md")
    parser.add_argument("--badge-out", default="docs/marc/telemetry-badge.json")
    args = parser.parse_args()

    records = token_telemetry_report.load_records(args.path)
    sessions = token_telemetry_report.group_by_session(records)

    generate_markdown(sessions, args.md_out)

    # Unconditional (origin: #285): always regenerate the badge in the same
    # invocation that just wrote the markdown, so badge and dashboard can
    # never disagree. generate_badge() reads the JSONL files itself (via
    # benchmark_report.median_weighted) rather than reusing `sessions`
    # above, which sums per-session — the badge needs the median-per-sample
    # methodology benchmark_report.py already implements (issue #296).
    generate_badge(args.baseline, args.path, args.neutral_baseline, args.neutral_path, args.badge_out)

if __name__ == "__main__":
    main()
