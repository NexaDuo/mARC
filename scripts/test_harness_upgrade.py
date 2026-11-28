#!/usr/bin/env python3
"""Cross-repo install + upgrade path for Codex, Copilot and Antigravity (#368).

    python3 scripts/test_harness_upgrade.py HARNESS [--prev-ref REF] [--seed-break KIND]

HARNESS is codex, copilot or antigravity. Stdlib only, no model call, no
secret. CI/test infrastructure: not distributed in the plugin. Everything runs
in a throwaway HOME (+ CODEX_HOME / COPILOT_HOME) and a consumer dir outside
the checkout; the developer's real CLI state is never touched.

(a) Fresh install of the PR build from the consumer dir; every component
    registered, installed version and file contents equal the PR tree.
(b) Install the REF (default origin/main) build, then point at the PR build and
    run the closest REAL user path, probed per CLI (see the PR for evidence):
      codex        no `plugin update`; `plugin marketplace upgrade` only
                   refreshes Git marketplaces. Real path: re-run `plugin add`
                   after the local marketplace checkout moves to the PR build.
      copilot      `plugin update marc@nexaduo` (needs a version change; it
                   reports "already at latest" otherwise).
      antigravity  no update command. Real path: `plugin install <dir>` again,
                   which replaces the existing installation.
    Then assert the installed version is the PR's, every PR file is present
    with PR content, nothing that only the old build had survives, and every
    component is still registered.

When REF's version equals the PR's (the usual non-release PR) the old build is
stamped with a lower version and one extra file (copy in a temp dir only): update
commands are version-driven and a real upgrade always bumps the version.

--seed-break proves each assertion can fail (PR record, never CI):
  no-update       the update step is skipped
  no-bump         the PR build keeps the old version
  stale-file      an old-build-only file survives the upgrade
  drop-component  a component is missing from the upgraded install
  inside-checkout the consumer dir is placed inside the checkout
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from local_test_marketplace import prepare  # noqa: E402
from test_installed_harness import base_env, sh  # noqa: E402

OLD_VERSION = "0.0.1-prev368"
STALE = "MARC-PREV-ONLY-368.txt"
failures: list[str] = []


def check(ok: bool, msg: str) -> bool:
    print(("PASS: " if ok else "FAIL: ") + msg)
    if not ok:
        failures.append(msg)
    return ok


def digest(root: str) -> dict:
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".pyc"):
                continue
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def read_version(plugin_dir: str) -> str:
    with open(os.path.join(plugin_dir, "plugin.json"), encoding="utf-8") as f:
        return json.load(f)["version"]


def set_version(plugin_dir: str, version: str) -> None:
    path = os.path.join(plugin_dir, "plugin.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["version"] = version
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def build_prev(ref: str, dest: str) -> str:
    os.makedirs(dest)
    archive = subprocess.run(["git", "-C", ROOT, "archive", ref], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", dest], input=archive, check=True)
    compiler = os.path.join(dest, "scripts", "compile_prompts.py")
    if os.path.isfile(compiler):
        subprocess.run([sys.executable, compiler], cwd=dest, capture_output=True, check=True)
    return dest


def replace_tree(target: str, source: str) -> None:
    """Move a marketplace checkout in place to another build (what a `git pull` does)."""
    for name in os.listdir(target):
        path = os.path.join(target, name)
        shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.unlink(path)
    shutil.copytree(source, target, dirs_exist_ok=True)


# --------------------------------------------------------------------------- adapters
class Harness:
    name = ""
    def __init__(self, tmp: str, consumer: str):
        self.tmp, self.consumer = tmp, consumer
        self.market = os.path.join(tmp, "marketplace")  # fixed path, content moves between builds
        self.ctx: dict = {}
        self.installed = ""

    def src(self, build: str) -> str:
        return os.path.join(build, "harnesses", self.name, "marc")

    def env(self) -> dict:
        return base_env(self.ctx)

    def run(self, *argv, **kw):
        return sh(list(argv), env=self.env(), cwd=self.consumer, check=kw.pop("check", True), **kw)

    def components(self, build: str) -> list:
        """Relative paths of every component the PR build ships (skills and agents)."""
        plugin = self.src(build)
        found = []
        for sub in ("skills", "agents"):
            base = os.path.join(plugin, sub)
            if os.path.isdir(base):
                for entry in sorted(os.listdir(base)):
                    found.append(f"{sub}/{entry}")
        return found

    def install_build(self, build: str) -> None:
        raise NotImplementedError

    def upgrade(self, build: str) -> None:
        raise NotImplementedError

    def registered(self, build: str, label: str) -> None:
        raise NotImplementedError

    def drop_component(self, build: str) -> None:
        path = os.path.join(self.installed, self.components(build)[0])
        shutil.rmtree(path) if os.path.isdir(path) else os.unlink(path)


class Codex(Harness):
    name = "codex"

    def setup(self):
        home = os.path.join(self.tmp, "home")
        self.ctx = {"HOME": home, "CODEX_HOME": os.path.join(self.tmp, "codex-home")}
        for d in self.ctx.values():
            os.makedirs(d, exist_ok=True)

    def add_plugin(self) -> dict:
        out = self.run("codex", "plugin", "add", "marc@nexaduo", "--json").stdout
        info = json.loads(out)
        self.installed = info["installedPath"]
        return info

    def install_build(self, build):
        self.run("codex", "plugin", "marketplace", "add", self.market, "--json")
        self.add_plugin()

    def upgrade(self, build):
        # No `plugin update`, and `plugin marketplace upgrade` is Git-only.
        self.add_plugin()

    def registered(self, build, label):
        listing = json.loads(self.run("codex", "plugin", "list", "--json").stdout)["installed"]
        check(any(p["pluginId"] == "marc@nexaduo" and p["enabled"] and p["installed"] for p in listing),
              f"{label}: plugin list shows marc@nexaduo installed and enabled")
        check(os.path.realpath(self.installed).startswith(os.path.realpath(self.ctx["CODEX_HOME"])),
              f"{label}: installed copy is under the temp CODEX_HOME")
        missing = [c for c in self.components(build) if not os.path.exists(os.path.join(self.installed, c))]
        check(not missing, f"{label}: every skill and agent is in the installed copy (missing: {missing})")


class Copilot(Harness):
    name = "copilot"

    def setup(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        self.ctx = {"HOME": home, "COPILOT_HOME": os.path.join(home, ".copilot")}
        self.installed = os.path.join(home, ".copilot", "installed-plugins", "nexaduo", "marc")

    def install_build(self, build):
        self.run("copilot", "plugin", "marketplace", "add", self.market)
        self.run("copilot", "plugin", "install", "marc@nexaduo")

    def upgrade(self, build):
        self.run("copilot", "plugin", "update", "marc@nexaduo")

    def registered(self, build, label):
        check("marc@nexaduo" in self.run("copilot", "plugin", "list").stdout, f"{label}: plugin list shows marc@nexaduo")
        skills = self.run("copilot", "skill", "list").stdout
        for skill in ("init", "tech-lead", "eng-director"):
            check(re.search(rf"\b{skill}\b", skills) is not None, f"{label}: skill '{skill}' in copilot skill list")
        missing = [c for c in self.components(build) if not os.path.exists(os.path.join(self.installed, c))]
        check(not missing, f"{label}: every skill and agent is in the installed copy (missing: {missing})")


class Antigravity(Harness):
    name = "antigravity"

    def setup(self):
        self.ctx = {"HOME": os.path.join(self.tmp, "home")}
        os.makedirs(self.ctx["HOME"])
        self.installed = os.path.join(self.ctx["HOME"], ".gemini", "config", "plugins", "marc")

    def install_build(self, build):
        self.run("agy", "plugin", "install", self.src(build))

    upgrade = install_build  # no update command; installing again replaces the installation

    def registered(self, build, label):
        listing = json.loads(self.run("agy", "plugin", "list", "--json").stdout)
        entries = listing.get("imports", listing) if isinstance(listing, dict) else listing
        marc = next((p for p in entries if p.get("name") == "marc"), None)
        check(marc is not None and marc.get("enabled", True), f"{label}: agy plugin list shows marc enabled")
        have = set((marc or {}).get("skills", []))
        want = {c.split("/", 1)[1] for c in self.components(build) if c.startswith("skills/")}
        check(want <= have, f"{label}: every skill registered with agy (missing: {sorted(want - have)})")
        missing = [c for c in self.components(build) if not os.path.exists(os.path.join(self.installed, c))]
        check(not missing, f"{label}: every skill and agent is in the installed copy (missing: {missing})")


HARNESSES = {"codex": Codex, "copilot": Copilot, "antigravity": Antigravity}


def assert_content(h: Harness, build: str, label: str, absent: str | None = None) -> None:
    want = digest(h.src(build))
    got = digest(h.installed)
    bad = sorted(k for k, v in want.items() if got.get(k) != v)
    check(not bad, f"{label}: installed files equal the build's (differing: {bad[:5]})")
    if absent:
        check(absent not in got, f"{label}: file only the old build had ({absent}) is gone")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("harness", choices=sorted(HARNESSES))
    ap.add_argument("--prev-ref", default="origin/main")
    ap.add_argument("--seed-break", choices=("no-update", "no-bump", "stale-file", "drop-component", "inside-checkout"))
    args = ap.parse_args()
    seed = args.seed_break

    with tempfile.TemporaryDirectory(prefix=f"marc-{args.harness}-upgrade-") as tmp:
        consumer = os.path.join(ROOT, ".marc-consumer-368") if seed == "inside-checkout" else os.path.join(tmp, "consumer")
        os.makedirs(consumer, exist_ok=True)
        try:
            h = HARNESSES[args.harness](tmp, consumer)
            h.setup()
            real = os.path.realpath(consumer)
            check(not (real == ROOT or real.startswith(ROOT + os.sep)), f"consumer dir is outside the checkout ({real})")

            pr = prepare(os.path.join(tmp, "pr-build"))
            pr_version = read_version(h.src(pr))

            # (a) fresh install of the PR build, from outside the checkout.
            os.makedirs(os.path.join(tmp, "fresh"))
            fresh = HARNESSES[args.harness](os.path.join(tmp, "fresh"), consumer)
            fresh.setup()
            shutil.copytree(pr, fresh.market)
            fresh.install_build(pr)
            check(read_version(fresh.installed) == pr_version, f"(a) installed version is the PR's ({pr_version})")
            fresh.registered(pr, "(a) fresh install")
            assert_content(fresh, pr, "(a) fresh install")

            # (b) old build -> PR build.
            prev = prepare(os.path.join(tmp, "prev-build"), build_prev(args.prev_ref, os.path.join(tmp, "prev-src")))
            prev_plugin = h.src(prev)
            if read_version(prev_plugin) == pr_version:
                set_version(prev_plugin, OLD_VERSION)
            with open(os.path.join(prev_plugin, STALE), "w", encoding="utf-8") as f:
                f.write("exists only in the old build\n")
            prev_version = read_version(prev_plugin)
            check(prev_version != pr_version, f"old build ({prev_version}) differs from the PR build ({pr_version})")

            os.makedirs(h.market)
            shutil.copytree(prev, h.market, dirs_exist_ok=True)
            h.install_build(prev)
            check(read_version(h.installed) == prev_version, f"(b) before upgrade: installed version is the old build's ({prev_version})")
            check(os.path.isfile(os.path.join(h.installed, STALE)), "(b) before upgrade: installed copy is the old build")

            if seed == "no-bump":
                set_version(h.src(pr), prev_version)  # the PR build forgot to bump
            replace_tree(h.market, pr)
            if seed != "no-update":
                h.upgrade(pr)
            if seed == "stale-file":
                with open(os.path.join(h.installed, STALE), "w", encoding="utf-8") as f:
                    f.write("survivor\n")
            if seed == "drop-component":
                h.drop_component(pr)

            check(read_version(h.installed) == pr_version, f"(b) after upgrade: installed version is the PR's ({pr_version})")
            assert_content(h, pr, "(b) after upgrade", absent=STALE)
            h.registered(pr, "(b) after upgrade")
        finally:
            if seed == "inside-checkout":
                shutil.rmtree(consumer, ignore_errors=True)

    if failures:
        print(f"\n{args.harness}: {len(failures)} assertion(s) FAILED")
        return 1
    print(f"\n{args.harness}: cross-repo install and upgrade path PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
