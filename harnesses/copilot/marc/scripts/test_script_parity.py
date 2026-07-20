#!/usr/bin/env python3
"""Cross-harness script-parity self-test (origin: #107).

Stdlib only (no pytest); run directly:  python3 test_script_parity.py

Bug this guards: a harness's compiled SKILL.md can instruct the operator to
run `${SOME_PLUGIN_ROOT}/scripts/<name>.py`, while that harness ships no
`scripts/` directory at all (e.g. Antigravity referenced
`${AGY_PLUGIN_ROOT}/scripts/board_reconcile.py` with no `scripts/` symlink —
issue #107). That drift is invisible to prose review and to
`compile_prompts.py` idempotency (the placeholder substitution itself is
correct; only the shipped filesystem is missing the target). This test closes
that gap: for every harness, scan its compiled SKILL.md files for
`${<ITS_PLUGIN_ROOT_ENV>:-.}/scripts/<name>` references and assert each one
resolves to a real file within that harness's own `marc/` directory (symlinks
follow, per `harnesses/*/marc/scripts` mirroring `harnesses/*/marc/hooks`).

Deterministic, offline, zero token cost. No real repo, no network, no `gh`/
`git` calls.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# harnesses/claude-code/marc/scripts -> repo root is four levels up.
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
HARNESSES_DIR = os.path.join(REPO_ROOT, "harnesses")

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


def find_script_refs(skill_md_text: str, plugin_root_env: str) -> set[str]:
    """Return the set of `scripts/<name>` basenames referenced via
    `${<plugin_root_env>:-.}/scripts/<name>` in the given compiled SKILL.md
    text."""
    pattern = re.compile(
        r"\$\{" + re.escape(plugin_root_env) + r":-\.\}/scripts/([A-Za-z0-9_.\-]+)"
    )
    return set(pattern.findall(skill_md_text))


def main() -> int:
    if not os.path.isdir(HARNESSES_DIR):
        print(f"::error::harnesses/ directory not found at {HARNESSES_DIR}")
        return 1

    harnesses = sorted(
        d for d in os.listdir(HARNESSES_DIR)
        if os.path.isdir(os.path.join(HARNESSES_DIR, d, "marc"))
    )
    check(len(harnesses) >= 2, f"discovered >=2 harnesses: {harnesses}")

    total_refs = 0
    for harness in harnesses:
        marc_dir = os.path.join(HARNESSES_DIR, harness, "marc")
        compile_json_path = os.path.join(marc_dir, "compile.json")
        if not os.path.isfile(compile_json_path):
            check(False, f"{harness}: missing compile.json at {compile_json_path}")
            continue

        with open(compile_json_path, "r", encoding="utf-8") as f:
            compile_config = json.load(f)

        plugin_root_env = compile_config.get("plugin_root_env")
        check(
            bool(plugin_root_env),
            f"{harness}: compile.json has a non-empty 'plugin_root_env'",
        )
        if not plugin_root_env:
            continue

        skill_md_files = sorted(
            glob.glob(os.path.join(marc_dir, "skills", "*", "SKILL.md"))
        )
        check(
            len(skill_md_files) > 0,
            f"{harness}: found >=1 compiled SKILL.md under {marc_dir}/skills/",
        )

        harness_refs: set[str] = set()
        for skill_md in skill_md_files:
            with open(skill_md, "r", encoding="utf-8") as f:
                text = f.read()
            harness_refs |= find_script_refs(text, plugin_root_env)

        for script_name in sorted(harness_refs):
            total_refs += 1
            resolved = os.path.join(marc_dir, "scripts", script_name)
            check(
                os.path.isfile(resolved),
                f"{harness}: '${{{plugin_root_env}}}/scripts/{script_name}' "
                f"resolves to a real file ({resolved})",
            )

    check(total_refs > 0, f"scanned >=1 script reference across all harnesses (got {total_refs})")

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1

    print(f"\nCross-harness script-parity gate: OK ({total_refs} script reference(s) verified across {len(harnesses)} harness(es)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
