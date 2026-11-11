#!/usr/bin/env python3
"""compile_prompts.mirror_agent_configs never copies a symlink (PR #387 SEC-1, #385).

Only regular `*.toml` files under core/agent-configs/{codex,antigravity}/ may
reach a shipped plugin. No model calls, no network.
"""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compile_prompts  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        outside = os.path.join(tmp, "outside")
        os.makedirs(outside)
        secret = os.path.join(outside, "secret.toml")
        with open(secret, "w") as f:
            f.write('token = "x"\n')
        src = os.path.join(tmp, "core", "agent-configs")
        os.makedirs(os.path.join(src, "codex"))
        with open(os.path.join(src, "codex", "engineer.toml"), "w") as f:
            f.write('model = "m"\n')
        with open(os.path.join(src, "codex", "notes.md"), "w") as f:
            f.write("not a descriptor\n")
        os.symlink(secret, os.path.join(src, "codex", "leak.toml"))      # file symlink
        os.symlink(outside, os.path.join(src, "antigravity"))            # dir symlink
        os.makedirs(os.path.join(src, "other"))                          # unknown subdir
        with open(os.path.join(src, "other", "x.toml"), "w") as f:
            f.write("x = 1\n")
        dest = os.path.join(tmp, "plugin", "agent-configs")
        os.makedirs(os.path.join(dest, "codex"))
        with open(os.path.join(dest, "codex", "stale.toml"), "w") as f:
            f.write("stale = 1\n")

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            skipped = compile_prompts.mirror_agent_configs(src, dest)

        shipped = sorted(os.path.relpath(os.path.join(r, n), dest)
                         for r, _, names in os.walk(dest) for n in names)
        checks = [
            (shipped == ["codex/engineer.toml"], f"only the regular descriptor ships: {shipped}"),
            (not any(os.path.islink(os.path.join(r, n)) for r, ds, ns in os.walk(dest) for n in ds + ns),
             "no symlink in the destination"),
            (len(skipped) == 2, f"both symlinks reported as skipped: {skipped}"),
            ("refusing to copy symlink" in err.getvalue(), "a warning names the refused symlink"),
        ]
    failed = [msg for ok, msg in checks if not ok]
    for ok, msg in checks:
        print(("PASS" if ok else "FAIL") + f": {msg}")
    if failed:
        return 1
    print("mirror_agent_configs symlink gate: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
