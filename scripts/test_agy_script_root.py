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
     plugin list has no enabled marc entry, gives a relative path, lists two
     distinct enabled roots, is not parseable JSON, or agy is missing;
  5. a workspace `json.py` or `json/` package never runs: the resolver is
     `python3 -I` (@sec SEC-1 on PR #366).

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


def _resolver_program() -> str:
    """The resolver under test. MARC_SEED_RESOLVER=<path to an older
    compile_prompts.py> swaps it, to prove the fixtures below fail on the
    round-0 resolver (#371 REV-2); never set in CI."""
    seed = os.environ.get("MARC_SEED_RESOLVER")
    if not seed:
        return compile_prompts._PLUGIN_LIST_RESOLVER
    import importlib.util
    spec = importlib.util.spec_from_file_location("seed_compile_prompts", seed)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._PLUGIN_LIST_RESOLVER


def resolver_fixtures() -> None:
    """Fixtures for the Claude Code, Codex and Copilot list formats (#367,
    #371 REV-1/REV-2), shaped like the real outputs: `claude plugin list
    --json` lists every scope and every project's project-scope install,
    each `enabled: true`; Codex lists per-user installs without paths;
    Copilot's config.json carries `//` lines and `installedPlugins`."""
    program = _resolver_program()
    with tempfile.TemporaryDirectory() as tmp:
        def root(name):
            p = os.path.join(tmp, "roots", name)
            os.makedirs(os.path.join(p, "scripts"), exist_ok=True)
            return p
        proj = os.path.join(tmp, "proj")
        os.makedirs(os.path.join(proj, ".git"))
        os.makedirs(os.path.join(proj, "sub"))
        elsewhere = os.path.join(tmp, "elsewhere")
        os.makedirs(elsewhere)
        marker = os.path.join(tmp, "decoy-json-ran")
        for ws in (proj, os.path.join(proj, "sub"), elsewhere):
            with open(os.path.join(ws, "json.py"), "w", encoding="utf-8") as f:
                f.write(f"open({marker!r}, 'a').write('x')\n")
        codex_home = os.path.join(tmp, "codex-home")
        U, P, L, O1, O2, P2 = (root(n) for n in ("user", "proj", "local", "other1", "other2", "proj-dup"))

        def cc(scope, path, install, enabled=True, plugin="marc@mkt"):
            return {"id": plugin, "scope": scope, "projectPath": path, "installPath": install,
                    "enabled": enabled, "version": "1"}
        claude_real = [cc("user", None, U), cc("project", os.path.join(tmp, "other1"), O1),
                       cc("project", proj, P), cc("project", os.path.join(tmp, "other2"), O2),
                       cc("synced", None, ""), cc("user", None, root("x"), plugin="other@mkt")]
        codex_root = os.path.join(codex_home, "plugins", "cache", "mkt", "marc", "2")
        os.makedirs(os.path.join(codex_root, "scripts"))
        os.makedirs(os.path.join(codex_home, "plugins", "cache", "mkt2", "marc", "3", "scripts"))
        os.makedirs(os.path.join(codex_home, "plugins", "cache", "mkt", "scripts"))  # what `..` would reach
        cx = lambda m, v, **k: dict({"pluginId": f"marc@{m}", "name": "marc", "marketplaceName": m,  # noqa: E731
                                     "version": v, "installed": True, "enabled": True,
                                     "source": {"source": "local", "path": proj}}, **k)
        cp = lambda path, **k: dict({"name": "marc", "marketplace": "mkt", "version": "1",  # noqa: E731
                                     "cache_path": path, "enabled": True}, **k)
        copilot = lambda entries: ("// User settings belong in settings.json.\n// managed\n"  # noqa: E731
                                   + json.dumps({"firstLaunchAt": "x", "installedPlugins": entries}))
        cases = [
            ("claude multi-scope: this repo's project entry wins", claude_real, proj, P),
            ("claude multi-scope from a subdir of the repo", claude_real, os.path.join(proj, "sub"), P),
            ("claude outside any listed project: user scope", claude_real, elsewhere, U),
            ("claude local beats project", claude_real + [cc("local", proj, L)], proj, L),
            ("claude disabled project entry falls back to user",
             [cc("user", None, U), cc("project", proj, P, enabled=False)], proj, U),
            ("claude real tie at project scope", claude_real + [cc("project", proj, P2)], proj, None),
            ("claude only other projects' entries", [cc("project", os.path.join(tmp, "other1"), O1)], proj, None),
            ("codex: cache path from marketplace + version", {"installed": [cx("mkt", "2")]}, proj, codex_root),
            ("codex: disabled second marketplace ignored",
             {"installed": [cx("mkt", "2"), cx("mkt2", "3", enabled=False)]}, proj, codex_root),
            ("codex: two enabled marketplaces is a tie", {"installed": [cx("mkt", "2"), cx("mkt2", "3")]}, proj, None),
            ("codex: traversal in version refused", {"installed": [cx("mkt", "..")]}, proj, None),
            ("copilot config.json with // header", copilot([cp(P)]), proj, P),
            ("copilot disabled install ignored", copilot([cp(P), cp(O1, enabled=False, marketplace="m2")]), proj, P),
            ("copilot two enabled installs is a tie", copilot([cp(P), cp(O1, marketplace="m2")]), proj, None),
        ]
        env = {k: v for k, v in os.environ.items() if "PLUGIN" not in k}
        env["CODEX_HOME"] = codex_home
        for label, data, cwd, want in cases:
            p = subprocess.run([sys.executable, "-I", "-c", program], input=data if isinstance(data, str)
                               else json.dumps(data), cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
            got = p.stdout.strip()
            ran = os.path.exists(marker)
            if ran:
                os.remove(marker)
            if want:
                ok = got == os.path.join(os.path.realpath(want), "scripts")
            else:
                ok = got == "/marc-plugin-root-unresolved" and "[mARC] " in p.stderr \
                    and "refusing to fall back to the workspace" in p.stderr
            check(ok and not ran and "Traceback" not in p.stderr,
                  f"resolver fixture: {label} (got {got!r}, err {p.stderr.strip()[:120]!r})")


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

        # Fake installed plugins, fake agy, and two hostile workspaces: each has
        # decoy ./scripts/<name>.py and a decoy stdlib `json` (a json.py module
        # or a json/ package) that records it ran and points at the workspace.
        # The resolver must run `python3 -I` so the decoy never imports
        # (@sec SEC-1). The fake installed scripts `import json` too: the outer
        # `python3 "<root>/scripts/<name>.py"` puts the script's own dir, not
        # the cwd, first on sys.path, so the decoy must not run there either.
        root = os.path.join(tmp, "installed", "marc")
        root2 = os.path.join(tmp, "installed2", "marc")
        marker = os.path.join(tmp, "decoy-json-ran")
        workspaces = {"json.py": os.path.join(tmp, "ws-module"),
                      "json/__init__.py": os.path.join(tmp, "ws-package")}
        decoy_json = (f"open({marker!r}, 'a').write('ran')\n"
                      "import os\n"
                      "def load(*a, **k): return [{'name': 'marc', 'path': os.getcwd()}]\n"
                      "loads = load\n")
        for base, tag in [(root, "INSTALLED"), (root2, "INSTALLED2")] + [
                (w, "WORKSPACE-DECOY") for w in workspaces.values()]:
            os.makedirs(os.path.join(base, "scripts"))
            for n in names:
                with open(os.path.join(base, "scripts", n), "w", encoding="utf-8") as f:
                    f.write(f"import json\nprint('{tag} {n}')\n")
        for rel, w in workspaces.items():
            os.makedirs(os.path.dirname(os.path.join(w, rel)), exist_ok=True)
            with open(os.path.join(w, rel), "w", encoding="utf-8") as f:
                f.write(decoy_json)
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        os.symlink(sys.executable, os.path.join(bindir, "python3"))
        listing = os.path.join(tmp, "listing.json")
        agy = os.path.join(bindir, "agy")
        with open(agy, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable} -I\nimport sys\nsys.stdout.write(open({listing!r}).read())\n")
        os.chmod(agy, 0o755)
        nobin = os.path.join(tmp, "nobin")
        os.makedirs(nobin)
        os.symlink(sys.executable, os.path.join(nobin, "python3"))

        def with_listing(data) -> None:
            with open(listing, "w", encoding="utf-8") as f:
                f.write(data if isinstance(data, str) else json.dumps(data))

        def decoy_ran() -> bool:
            ran = os.path.exists(marker)
            if ran:
                os.remove(marker)
            return ran

        check("python3 -I -c" in expr, "resolver runs python3 isolated (-I)")
        entry = {"name": "marc", "enabled": True, "path": root}
        ok_cases = (
            ("list", [{"name": "other", "path": root2}, entry]),
            ("imports", {"imports": [entry]}),
            ("disabled duplicate skipped", [dict(entry, enabled=False, path=root2), entry]),
            ("same root listed twice", [entry, dict(entry)]),
        )
        for shape, data in ok_cases:
            with_listing(data)
            for kind, ws in workspaces.items():
                for n in names:
                    p = run(f'python3 "{expr}/{n}"', ws, bindir)
                    check(p.returncode == 0 and p.stdout.strip() == f"INSTALLED {n}"
                          and not decoy_ran(),
                          f"{shape} listing, decoy {kind}: {n} runs from the installed root "
                          f"(rc={p.returncode} out={p.stdout.strip()!r} err={p.stderr.strip()[:200]!r})")

        n = names[0]
        cases = (
            ("no marc entry", bindir, [{"name": "other", "path": root}], "no installed marc"),
            ("relative path", bindir, [{"name": "marc", "path": "."}], "no installed marc"),
            ("marc root without scripts/", bindir, [{"name": "marc", "path": tmp}], "no installed marc"),
            ("only entry disabled", bindir, [dict(entry, enabled=False)], "no installed marc"),
            ("two enabled roots", bindir, [entry, dict(entry, path=root2)], "ambiguous"),
            ("malformed JSON", bindir, '[{"name": "marc", "path": ', "no installed marc"),
            ("non-list JSON", bindir, "42", "no installed marc"),
            ("agy not on PATH", nobin, [], "no installed marc"),
        )
        for label, path, data, reason in cases:
            with_listing(data)
            for kind, ws in workspaces.items():
                p = run(f'python3 "{expr}/{n}"', ws, path)
                check(p.returncode != 0 and "DECOY" not in p.stdout
                      and "INSTALLED" not in p.stdout and not decoy_ran(),
                      f"fails closed ({label}, decoy {kind}): rc={p.returncode} out={p.stdout.strip()!r}")
                check("[mARC] " in p.stderr and reason in p.stderr
                      and "refusing to fall back to the workspace" in p.stderr
                      and "Traceback" not in p.stderr,
                      f"clear [mARC] error on stderr ({label}, decoy {kind}): {p.stderr.strip()[:200]!r}")

    resolver_fixtures()

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("\nAntigravity bundled-script root: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
