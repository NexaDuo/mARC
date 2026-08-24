#!/usr/bin/env python3
"""Cross-harness hooks-parity self-test (origin: #173, #170, #197).

Stdlib only (no pytest); run directly: python3 test_hooks_parity.py

`hooks.json` used to be the one load-bearing plugin component NOT compiled
from `core/` (issue #173): each harness hand-maintained its own copy, and
`harnesses/antigravity/marc/hooks` was a bare symlink to Claude Code's
`hooks/` that hardcoded `CLAUDE_PLUGIN_ROOT` with no fallback, so every
Antigravity hook silently no-op'd (issue #170). This test closes the gap the
same way `test_script_parity.py` already closes it for `scripts/`:

  * DRIFT: recompute what `scripts/compile_prompts.py` would emit for every
    harness's native `hooks.json` (at plugin root for Antigravity, or
    `hooks/hooks.json` for Claude Code / Copilot) from `core/hooks/hooks.spec.json`
    and assert it is byte-identical to the committed file. A hand-edit to a
    harness's `hooks.json` (bypassing `core/`) fails this check.
  * EXISTENCE: for every harness, every hook-script reference embedded in its
    compiled `hooks.json` must resolve to a real, executable-looking `.sh`
    file physically present under that harness's own `marc/hooks/` — no
    symlink required, no cross-harness reach-through.
  * NOT-A-SYMLINK: no harness's `marc/hooks/` may be a symlink (the exact
    shape of the #170 defect) — it must be a real, self-contained, compiled
    directory, because a subpath-only checkout of one harness's tree (e.g. a
    `owner/repo:harnesses/copilot/marc` install) cannot follow a symlink that
    points outside that subtree.

Deterministic, offline, zero token cost. No real repo, no network, no `gh`/
`git` calls.
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo_root(start: str) -> str:
    d = start
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "core")) and os.path.isdir(os.path.join(d, "harnesses")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.abspath(os.path.join(start, "..", ".."))


REPO_ROOT = _find_repo_root(HERE)
HARNESSES_DIR = os.path.join(REPO_ROOT, "harnesses")
CORE_DIR = os.path.join(REPO_ROOT, "core")

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import compile_prompts  # noqa: E402

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


def find_hook_script_refs(hooks_json_text: str) -> set[str]:
    """Return the set of hooks/<name>.sh basenames referenced anywhere in the
    compiled hooks.json text (covers both the Claude Code/Antigravity
    `${VAR}/hooks/<name>.sh` shape and any future dialect using the same
    convention)."""
    return set(re.findall(r"/hooks/([A-Za-z0-9_.\-]+\.sh)", hooks_json_text))


def validate_hook_schema(harness: str, dialect: str, hooks_obj: dict) -> None:
    """Validate that the compiled hooks_obj conforms to the dialect's structural schema."""
    if dialect == "antigravity":
        check(isinstance(hooks_obj, dict), f"{harness}: antigravity hooks.json is a dict")
        check("hooks" not in hooks_obj, f"{harness}: antigravity hooks.json does not use top-level 'hooks' key")
        for hook_id, events in hooks_obj.items():
            check(isinstance(events, dict), f"{harness}: hook '{hook_id}' maps to an event dict")
            for event_name, handlers in events.items():
                check(
                    event_name in {"PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop"},
                    f"{harness}: hook '{hook_id}' event '{event_name}' is a valid Antigravity event",
                )
                check(isinstance(handlers, list), f"{harness}: hook '{hook_id}' event '{event_name}' is a list")
                if event_name in {"PreToolUse", "PostToolUse"}:
                    for entry in handlers:
                        check(
                            isinstance(entry, dict) and "matcher" in entry and "hooks" in entry,
                            f"{harness}: hook '{hook_id}' event '{event_name}' handler has matcher and hooks list",
                        )
                        check(isinstance(entry.get("hooks"), list), f"{harness}: hook '{hook_id}' hooks is a list")
                        for h in entry.get("hooks", []):
                            check(
                                h.get("type") == "command" and bool(h.get("command")),
                                f"{harness}: hook '{hook_id}' command handler is valid",
                            )
                else:
                    for h in handlers:
                        check(
                            isinstance(h, dict) and h.get("type") == "command" and bool(h.get("command")),
                            f"{harness}: hook '{hook_id}' event '{event_name}' command handler is valid",
                        )
    elif dialect == "claude-code":
        check(
            isinstance(hooks_obj, dict) and "hooks" in hooks_obj,
            f"{harness}: claude-code hooks.json has top-level 'hooks' key",
        )
    elif dialect == "copilot":
        check(
            isinstance(hooks_obj, dict) and hooks_obj.get("version") == 1 and "hooks" in hooks_obj,
            f"{harness}: copilot hooks.json has version: 1 and 'hooks' key",
        )


def main() -> int:
    if not os.path.isdir(HARNESSES_DIR):
        print(f"::error::harnesses/ directory not found at {HARNESSES_DIR}")
        return 1

    spec_path = os.path.join(CORE_DIR, "hooks", "hooks.spec.json")
    check(os.path.isfile(spec_path), f"core/hooks/hooks.spec.json exists at {spec_path}")
    if not os.path.isfile(spec_path):
        return 1

    harnesses = sorted(
        d for d in os.listdir(HARNESSES_DIR)
        if os.path.isdir(os.path.join(HARNESSES_DIR, d, "marc"))
    )
    check(len(harnesses) >= 2, f"discovered >=2 harnesses: {harnesses}")

    hook_dialect_harnesses = 0
    total_script_refs = 0

    for harness in harnesses:
        marc_dir = os.path.join(HARNESSES_DIR, harness, "marc")
        compile_json_path = os.path.join(marc_dir, "compile.json")
        if not os.path.isfile(compile_json_path):
            check(False, f"{harness}: missing compile.json at {compile_json_path}")
            continue

        with open(compile_json_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        dialect = config.get("hook_dialect")
        if not dialect:
            print(f"SKIP: {harness} declares no 'hook_dialect' in compile.json (opt-out of hooks entirely)")
            continue
        hook_dialect_harnesses += 1

        dest_hooks_dir = os.path.join(marc_dir, "hooks")

        check(
            not os.path.islink(dest_hooks_dir),
            f"{harness}: marc/hooks is a real directory, not a symlink (the exact #170 defect shape)",
        )
        check(os.path.isdir(dest_hooks_dir), f"{harness}: marc/hooks/ exists as a directory")

        dest_hooks_json = compile_prompts.get_hooks_json_path(marc_dir, config)
        rel_hooks_json = os.path.relpath(dest_hooks_json, marc_dir)
        check(os.path.isfile(dest_hooks_json), f"{harness}: {rel_hooks_json} exists at {dest_hooks_json}")
        if not os.path.isfile(dest_hooks_json):
            continue

        if rel_hooks_json == "hooks.json":
            stale_hooks_json = os.path.join(dest_hooks_dir, "hooks.json")
            check(
                not os.path.isfile(stale_hooks_json),
                f"{harness}: no stale hooks/hooks.json present (hooks.json is at plugin root)",
            )
        elif rel_hooks_json == os.path.join("hooks", "hooks.json"):
            stale_hooks_json = os.path.join(marc_dir, "hooks.json")
            check(
                not os.path.isfile(stale_hooks_json),
                f"{harness}: no stale root hooks.json present (hooks.json is at hooks/hooks.json)",
            )

        with open(spec_path, "r", encoding="utf-8") as f:
            spec = json.load(f)
        hook_ids = set(config.get("hook_ids", []))
        check(len(hook_ids) > 0, f"{harness}: compile.json declares >=1 'hook_ids' entry")
        selected_hooks = [h for h in spec["hooks"] if h["id"] in hook_ids]

        renderer = compile_prompts._HOOK_DIALECT_RENDERERS.get(dialect)
        check(renderer is not None, f"{harness}: hook_dialect {dialect!r} has a known renderer")
        if renderer is None:
            continue
        try:
            expected = renderer(selected_hooks, config)
        except ValueError as e:
            # The compiler's own fail-closed shell-safety validator
            # (`_assert_shell_safe`, origin: #173) rejected a spec/config
            # value — a quote/backtick/`$`/`;`/newline that would splice
            # unescaped shell into hooks.json. Surface it as a normal gate
            # failure rather than an uncaught traceback.
            check(False, f"{harness}: hooks.spec.json/compile.json value is not shell-safe: {e}")
            continue

        with open(dest_hooks_json, "r", encoding="utf-8") as f:
            actual = json.load(f)

        check(
            expected == actual,
            f"{harness}: committed {rel_hooks_json} matches what core/hooks/hooks.spec.json compiles to "
            "(no hand-edit drift)",
        )

        validate_hook_schema(harness, dialect, actual)

        with open(dest_hooks_json, "r", encoding="utf-8") as f:
            raw_text = f.read()
        refs = find_hook_script_refs(raw_text)
        for name in sorted(refs):
            total_script_refs += 1
            resolved = os.path.join(dest_hooks_dir, name)
            check(
                os.path.isfile(resolved),
                f"{harness}: hook script '{name}' referenced from {rel_hooks_json} resolves to a real file ({resolved})",
            )

    check(hook_dialect_harnesses >= 1, f"found >=1 harness declaring hook_dialect (got {hook_dialect_harnesses})")
    check(total_script_refs > 0, f"scanned >=1 hook-script reference across all harnesses (got {total_script_refs})")

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1

    print(
        f"\nCross-harness hooks-parity gate: OK ({hook_dialect_harnesses} harness(es) with hooks, "
        f"{total_script_refs} hook-script reference(s) verified)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
