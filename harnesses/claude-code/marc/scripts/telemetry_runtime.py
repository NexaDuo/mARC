"""Explicit runtime boundary for Claude-only transcript tools (#330)."""
import json
import os
import sys

def add_harness_argument(parser):
    """Default to the installed plugin's own harness, with an explicit override."""
    default = "codex" if os.environ.get("CODEX_PROJECT_DIR") or os.environ.get("CODEX_THREAD_ID") else "claude-code"
    config = os.path.join(os.path.dirname(__file__), "..", "compile.json")
    try:
        with open(config, encoding="utf-8") as fh:
            default = "codex" if json.load(fh).get("hook_dialect") == "codex" else "claude-code"
    except (OSError, ValueError):
        pass
    parser.add_argument("--harness", default=default,
                        choices=("claude-code", "codex"),
                        help="transcript format (default: installed harness); only Claude Code is supported")


def unsupported_harness(harness):
    if harness == "claude-code":
        return False
    print(f"[mARC telemetry] {harness} telemetry unavailable: no validated transcript adapter. "
          "No transcripts were read and no telemetry was written. "
          "Use --harness claude-code explicitly to process Claude Code data.", file=sys.stderr)
    return True

