#!/usr/bin/env python3
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../core/scripts'))
import token_telemetry_report

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

def generate_badge(baseline_sessions, current_sessions, output_path):
    """Write the "Tokens Saved" shields.io endpoint badge.

    Unconditional by design (origin: #285): this is always called from
    main(), in the same invocation/run that writes the markdown dashboard
    from the same `current_sessions` data, so the badge can never be left
    stale while the dashboard moves on. When there is no real baseline (or
    no real current data) to compare, it writes the honest "No Data"/
    inactive state instead of a fabricated or stale percentage — this is
    the same reachable empty state PR #279 established, never traded away.
    """
    no_data_badge = {
        "schemaVersion": 1,
        "label": "Tokens Saved",
        "message": "No Data",
        "color": "inactive",
    }

    if not baseline_sessions or not current_sessions:
        badge = no_data_badge
    else:
        base_weighted = sum(token_telemetry_report.session_totals(t)["weighted"] for t in baseline_sessions.values())
        post_weighted = sum(token_telemetry_report.session_totals(t)["weighted"] for t in current_sessions.values())

        if base_weighted > 0:
            pct = ((base_weighted - post_weighted) / base_weighted) * 100
            badge = {
                "schemaVersion": 1,
                "label": "Tokens Saved",
                "message": f"{pct:.1f}%",
                "color": "success" if pct > 0 else "orange",
            }
        else:
            # A baseline file was provided but carries no measured tokens —
            # still honest "No Data", not a divide-by-zero 0.0%.
            badge = no_data_badge

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(badge, f, indent=2)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True,
                         help="current/post-optimization telemetry JSONL (drives the markdown trend chart)")
    parser.add_argument("--baseline", required=False,
                         help="baseline (pre-optimization / previous-release) telemetry JSONL to diff "
                              "against --path for the 'Tokens Saved' badge; omit for an honest 'No Data' badge")
    parser.add_argument("--md-out", default="docs/marc/telemetry.md")
    parser.add_argument("--badge-out", default="docs/marc/telemetry-badge.json")
    args = parser.parse_args()

    records = token_telemetry_report.load_records(args.path)
    sessions = token_telemetry_report.group_by_session(records)

    generate_markdown(sessions, args.md_out)

    baseline_sessions = None
    if args.baseline:
        baseline_records = token_telemetry_report.load_records(args.baseline)
        baseline_sessions = token_telemetry_report.group_by_session(baseline_records)

    # Unconditional (origin: #285): always regenerate the badge from the
    # same `sessions` this run just used for the markdown, so badge and
    # dashboard can never disagree — a run that updates one always updates
    # the other, whether or not a real --baseline was supplied.
    generate_badge(baseline_sessions, sessions, args.badge_out)

if __name__ == "__main__":
    main()
