#!/usr/bin/env python3
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(md))

def generate_badge(base_sessions, post_sessions, output_path):
    base_weighted = sum(token_telemetry_report.session_totals(t)["weighted"] for t in base_sessions.values())
    post_weighted = sum(token_telemetry_report.session_totals(t)["weighted"] for t in post_sessions.values())
    
    if base_weighted > 0:
        pct = ((base_weighted - post_weighted) / base_weighted) * 100
    else:
        pct = 0.0
        
    badge = {
        "schemaVersion": 1,
        "label": "Tokens Saved",
        "message": f"{pct:.1f}%",
        "color": "success" if pct > 0 else "orange"
    }
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(badge, f, indent=2)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--compare", required=False)
    parser.add_argument("--md-out", default="docs/marc/telemetry.md")
    parser.add_argument("--badge-out", default="docs/marc/telemetry-badge.json")
    args = parser.parse_args()
    
    records = token_telemetry_report.load_records(args.path)
    sessions = token_telemetry_report.group_by_session(records)
    
    generate_markdown(sessions, args.md_out)
    
    if args.compare:
        post_records = token_telemetry_report.load_records(args.compare)
        post_sessions = token_telemetry_report.group_by_session(post_records)
        generate_badge(sessions, post_sessions, args.badge_out)

if __name__ == "__main__":
    main()
