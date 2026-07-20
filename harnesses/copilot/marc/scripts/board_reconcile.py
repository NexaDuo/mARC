#!/usr/bin/env python3
"""Deprecated shim for `board.py` (renamed, origin: #128).

`board_reconcile.py` was renamed to `board.py`. This shim is kept for one
release so existing callers keep working unchanged, including the
backward-compatible no-subcommand default (`board_reconcile.py --json`
still runs `reconcile`) and the `set-status`/`create` subcommands.

Delegates entirely to `board.py`, which must live alongside this file (both
compile from `core/scripts/` into the same per-harness `scripts/` directory).
Do not add logic here — this file will be removed after one release.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import board  # noqa: E402

if __name__ == "__main__":
    sys.exit(board.main())
