#!/usr/bin/env python3
"""Antigravity bundled-script root self-test (origin: #364).

Stdlib only; run directly:  python3 scripts/test_agy_script_root.py

Bug this guards: compiled Antigravity skills told the model to run
`python3 "${AGY_PLUGIN_ROOT:-.}/scripts/<name>.py"`. agy exports no
AGY_PLUGIN_ROOT to the model's shell, so `:-.` resolved to the WORKSPACE's
./scripts: wrong in every consumer repo, and in mARC itself it silently ran
the checked-out branch's scripts instead of the installed plugin's.

What this asserts, through the real compiler (scripts/compile_prompts.py):
  1. compiling core/ with the antigravity compile.json reproduces the
     committed references byte for byte (so the checks below cover what ships);
  2. no compiled antigravity skill/hook file carries a cwd fallback for
     bundled scripts (`${...:-.}/scripts`, `${...:-$PWD}/scripts`, `./scripts/`);
  3. every bundled-script invocation uses the plugin-list resolver, and that
     resolver, given a fake `agy plugin list --json`, runs the script from the
     fake INSTALLED root, even with a decoy ./scripts/<name>.py in the cwd;
  4. it fails closed (non-zero, clear [mARC] error, decoy never runs) when the
     plugin list has no marc entry, gives a relative path, or agy is missing.

Deterministic, offline, zero token cost.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import compile_prompts  # noqa: E402

AGY = os.path.join(REPO, "harnesses", "antigravity", "marc")
CORE = os.path.join(REPO, "core")
REFS = ["dispatch.md", "planning.md", "review-release.md"]

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


CWD_FALLBACK = re.compile(r"\$\{[A-Za-z_]+:-(\.|\$PWD)\}/scripts|(^|[\s\"'(])\./scripts/")
INVOCATION = re.compile(r'python3 "(.+?)/([A-Za-z0-9_.\-]+\.py)"')


def compile_config() -> dict:
    with open(os.path.join(AGY, "compile.json"), encoding="utf-8") as f:
        return compile_prompts.apply_derived_defaults(json.load(f))


def compile_refs(config: dict, out: str) -> dict[str, str]:
    texts = {}
    for name in REFS:
        dest = os.path.join(out, name)
        with contextlib.redirect_stdout(io.StringIO()):
            compile_prompts.compile_file(
                os.path.join(CORE, "skills", "tech-lead", "references", name), dest, config
            )
        with open(dest, encoding="utf-8") as f:
            texts[name] = f.read()
    return texts


def shipped_text_files() -> list[str]:
    files = [os.path.join(AGY, "hooks.json")]
    for sub in ("skills", "hooks"):
        for root, _, names in os.walk(os.path.join(AGY, sub)):
            files += [os.path.join(root, n) for n in names]
    return [f for f in files if os.path.isfile(f)]


def run(cmd: str, cwd: str, path: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if "PLUGIN" not in k}
    env["PATH"] = path
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-c", cmd],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
    )


def main() -> int:
    config = compile_config()
    expr = config["bundled_scripts_dir"]
    check(config.get("bundled_scripts_resolver") == "plugin-list",
          "antigravity compile.json selects the plugin-list resolver")
    check("agy plugin list --json" in expr and ":-" not in expr,
          "resolver expression uses `agy plugin list --json` and has no `:-` default")

    with tempfile.TemporaryDirectory() as tmp:
        compiled = compile_refs(config, os.path.join(tmp, "out"))
        for name, text in compiled.items():
            with open(os.path.join(AGY, "skills", "tech-lead", "references", name), encoding="utf-8") as f:
                check(f.read() == text, f"committed references/{name} matches compiler output")

        for path in shipped_text_files():
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            rel = os.path.relpath(path, REPO)
            check("AGY_PLUGIN_ROOT:-." not in text, f"{rel}: no `${{AGY_PLUGIN_ROOT:-.}}`")
            check(not CWD_FALLBACK.search(text), f"{rel}: no cwd fallback for bundled scripts")

        invocations = [m for t in compiled.values() for m in INVOCATION.findall(t)]
        names = sorted({n for _, n in invocations})
        check(len(names) >= 3, f"found bundled-script invocations: {names}")
        check(all(e == expr for e, _ in invocations),
              "every bundled-script invocation uses the plugin-list resolver")

        # Fake installed plugin, fake agy, and a decoy ./scripts in the workspace.
        root = os.path.join(tmp, "installed", "marc")
        ws = os.path.join(tmp, "workspace")
        for base, tag in ((root, "INSTALLED"), (ws, "WORKSPACE-DECOY")):
            os.makedirs(os.path.join(base, "scripts"))
            for n in names:
                with open(os.path.join(base, "scripts", n), "w", encoding="utf-8") as f:
                    f.write(f"print('{tag} {n}')\n")
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        os.symlink(sys.executable, os.path.join(bindir, "python3"))
        listing = os.path.join(tmp, "listing.json")
        agy = os.path.join(bindir, "agy")
        with open(agy, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\nimport sys\nsys.stdout.write(open({listing!r}).read())\n")
        os.chmod(agy, 0o755)
        nobin = os.path.join(tmp, "nobin")
        os.makedirs(nobin)
        os.symlink(sys.executable, os.path.join(nobin, "python3"))

        def with_listing(data) -> None:
            with open(listing, "w", encoding="utf-8") as f:
                json.dump(data, f)

        entry = {"name": "marc", "enabled": True, "path": root}
        for shape, data in (("list", [{"name": "other", "path": tmp}, entry]),
                            ("imports", {"imports": [entry]})):
            with_listing(data)
            for n in names:
                p = run(f'python3 "{expr}/{n}"', ws, bindir)
                check(p.returncode == 0 and p.stdout.strip() == f"INSTALLED {n}",
                      f"{shape} listing: {n} runs from the installed root "
                      f"(rc={p.returncode} out={p.stdout.strip()!r} err={p.stderr.strip()[:200]!r})")

        n = names[0]
        cases = (
            ("no marc entry", bindir, [{"name": "other", "path": root}]),
            ("relative path", bindir, [{"name": "marc", "path": "."}]),
            ("marc root without scripts/", bindir, [{"name": "marc", "path": tmp}]),
            ("agy not on PATH", nobin, []),
        )
        for label, path, data in cases:
            with_listing(data)
            p = run(f'python3 "{expr}/{n}"', ws, path)
            check(p.returncode != 0 and "WORKSPACE-DECOY" not in p.stdout
                  and "INSTALLED" not in p.stdout,
                  f"fails closed ({label}): rc={p.returncode} out={p.stdout.strip()!r}")
            if label != "agy not on PATH":
                check("[mARC] cannot resolve the installed marc plugin root" in p.stderr,
                      f"clear error on stderr ({label})")

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("\nAntigravity bundled-script root: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
