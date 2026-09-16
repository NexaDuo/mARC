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


def generate_badge(baseline_path, badge_current_path, neutral_no_guard_path, neutral_guarded_path, output_path):
    """Write the "Tokens Saved (Last Release)" shields.io endpoint badge.

    Unconditional by design (origin: #285): this is always called from
    main(), in the same invocation/run the markdown dashboard was just
    written from, so the badge can never be left stale while the dashboard
    moves on.

    Issue #303: `badge_current_path` MUST be the SHIPPED DEFAULT arm — the
    guard is opt-in and ships DISABLED (no `[token_guard]` in
    `.agents/team.toml`; the template ships it commented out) — never the
    guard-ON arm B (`post-<task>.jsonl`, guard=350). Before this fix the
    caller passed `post-control.jsonl` (arm B) here, so the badge measured a
    configuration nobody runs and could invert sign relative to what a real
    user experiences (run 34987534132: -68.9% from arm B vs +35.0% from the
    guard-off arm C `toggle_baseline-control.jsonl` — the SAME commit's own
    shipped-default behavior). The guard-off, same-commit arm C file is the
    correct "current" value to diff against the previous release's baseline.

    Issue #296 re-scope: the badge answers "tokens saved on THIS release",
    not a standing "mARC saves X%" property — 0%/no-signal is the correct,
    expected reading for most releases. Two states, in order:

      1. "No Data" (inactive) — no baseline/current data, or no `neutral`
         pair to measure a noise floor from. A missing noise floor falls
         back to "No Data", NEVER to an ungated raw number — publishing a
         delta without a floor to gate it is the exact failure that put a
         fabricated 15.2% on this badge before (issue #274/#279).
      2. A margin/band, e.g. "-40.0% ±31.5%" (issue #308) — the measured
         delta, plus or minus the measured noise floor, ALWAYS shown
         together. Issue #308 replaced the previous behavior of silently
         suppressing the number below a cutoff ("No Change") with this
         band: a badge that carries its own uncertainty is self-documenting
         about instrument quality in a way silent suppression is not, and
         it stops discarding information the operator may still want. The
         badge color still signals confidence: "success"/"orange" when the
         band's interval [pct - floor, pct + floor] excludes zero (the
         delta is bigger than the noise), "informational" when the interval
         straddles zero (the number cannot be told apart from noise, but
         is shown anyway, band and all).

    The noise floor MUST be a same-commit pairing (`neutral_no_guard_path`
    = arm C, guard=999999, vs `neutral_guarded_path` = arm B, guard=350 —
    the "Causal Proof" pairing scripts/benchmark_report.py also uses for
    `neutral`), never an inter-release pairing (arm A, the previous
    release, vs arm B). An inter-release `neutral` gap differs by both
    release AND threshold, so it would absorb ordinary release-to-release
    drift into the floor and silently inflate it over time as more releases
    ship, suppressing real findings — a mistake caught in review on PR #297
    before it shipped.

    Issue #308: the floor is now a MAD (median absolute deviation) over the
    pooled, de-duplicated `neutral` same-commit observations
    (benchmark_report.mad_floor), not the old "gap between two medians" —
    median-vs-median is a claim about central tendency, not dispersion.
    `scripts/run_token_benchmark.sh` deliberately reuses `post-<task>.jsonl`
    as `toggle_post-<task>.jsonl` (arm B is the guard-on side of both
    comparisons), so mad_floor() de-duplicates by content before pooling —
    it must never treat those as two independent samples. Run 34987534132's
    same-commit `neutral` pair measured this MAD-derived floor at ~31.5%
    (was 33.7% under the old median-vs-median estimator; the near-agreement
    here is coincidental, not a design guarantee — the two estimators
    diverge by an order of magnitude on other slices of the same data, see
    issue #308). The floor's numeric VALUE remains provisional per #298
    (n=5 same-commit runs is too few for any dispersion statistic, MAD
    included, to be trusted on its own) — this function fixes the estimator
    and the presentation, not that open question.

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

    if not baseline_path or not badge_current_path:
        write(no_data_badge)
        return

    baseline_sample = benchmark_report.median_weighted(baseline_path)
    current_sample = benchmark_report.median_weighted(badge_current_path)
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

    # Noise floor: a MAD (median absolute deviation) over the pooled,
    # de-duplicated `neutral` SAME-COMMIT observations (no-guard vs
    # guard=350). `neutral` cannot be affected by the guard, so this
    # dispersion IS the instrument's measurement noise, computed fresh from
    # this run's own data — never hardcoded (issue #296), and pooled by
    # distinct arm rather than by file count so the deliberate
    # post/toggle_post file reuse is never double-counted (issue #308).
    floor = None
    if neutral_no_guard_path and neutral_guarded_path:
        floor = benchmark_report.mad_floor([neutral_no_guard_path, neutral_guarded_path])

    if floor is None:
        # `neutral` is missing or yields no usable floor — publish nothing
        # rather than an ungated number (see the docstring above).
        write(no_data_badge)
        return

    pct = _pct_delta(base_median, post_median)
    floor_pct = floor.mad_pct
    lower, upper = pct - floor_pct, pct + floor_pct
    if lower > 0:
        color = "success"
    elif upper < 0:
        color = "orange"
    else:
        # The band straddles zero: indistinguishable from noise. Shown
        # anyway, band and all — issue #308 replaced silent suppression
        # ("No Change") with an always-visible margin.
        color = "informational"
    badge = {
        "schemaVersion": 1,
        "label": BADGE_LABEL,
        "message": f"{pct:.1f}% ±{floor_pct:.1f}%",
        "color": color,
    }
    write(badge)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True,
                         help="current/post-optimization telemetry JSONL (drives the markdown trend chart)")
    parser.add_argument("--baseline", required=False,
                         help="baseline (pre-optimization / previous-release) telemetry JSONL to diff "
                              "against --badge-current (or --path, if --badge-current is omitted) for the "
                              "'Tokens Saved (Last Release)' badge; omit for an honest 'No Data' badge")
    parser.add_argument("--badge-current", required=False,
                         help="'current release' telemetry JSONL that feeds the badge diff against "
                              "--baseline. Issue #303: this MUST be the SHIPPED DEFAULT arm (guard "
                              "disabled, e.g. the same-commit toggle_baseline-<task>.jsonl), never the "
                              "guard-ON arm B post-<task>.jsonl -- see generate_badge()'s docstring. "
                              "Defaults to --path (the dashboard trend-chart source) if omitted, for "
                              "backward compatibility with callers that only pass one 'current' file.")
    parser.add_argument("--neutral-no-guard", required=False,
                         help="'neutral' task telemetry JSONL from the SAME-COMMIT no-guard arm "
                              "(arm C / toggle_baseline, guard=999999). Paired with --neutral-guarded "
                              "for the noise floor the badge gates on -- MUST be same-commit as its "
                              "pair, never an inter-release baseline (see generate_badge()'s "
                              "docstring). Omit for an honest 'No Data' badge rather than an ungated "
                              "number.")
    parser.add_argument("--neutral-guarded", required=False,
                         help="'neutral' task telemetry JSONL from the SAME-COMMIT guard=350 arm "
                              "(arm B / toggle_post), paired with --neutral-no-guard for the noise "
                              "floor.")
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
    badge_current_path = args.badge_current or args.path
    generate_badge(args.baseline, badge_current_path, args.neutral_no_guard, args.neutral_guarded, args.badge_out)

if __name__ == "__main__":
    main()
