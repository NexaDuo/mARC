### 5. Track to done
Summarize: demand → issue/board link → specialist → status. Dispatches run in
the background — stay responsive, resume an agent by its id for the next
dependency-chain stage. Relay PR links and CI/deploy status as specialists
report; keep board `Status` in sync. Not complete at PR-open — immediately
dispatch `@sec` and `@rev` to review the PR, monitor CI to green, and follow
through the repo's release phases to validated success.

For each re-review, send the prior reviewed SHA, current HEAD, unresolved
finding IDs (`SEC-n`/`REV-n`), and any structurally failed optional pass from the prior
handoff. Request the compact return defined in `dispatch.md`; inspect report
sections on demand. Both independent verdicts must still cover current HEAD.
Do not repeat an inconclusive optional pass merely because a new round started.
When `@rev` reports a diff touching `team.toml`'s `[review].hot_surfaces`, run
`/code-review` at `high` yourself; a subagent cannot. (origin: #125 · 2026-07-16)

**Verifying a version bump actually shipped** (tag, workflow, release):
```bash
S="$(cat "${COPILOT_HOME:-$HOME/.copilot}/config.json" | python3 -I -c 'exec("import json, os, sys\nj = os.path.join\ntry:\n    d = json.loads(\"\\n\".join(x for x in sys.stdin.read().splitlines() if not x.lstrip().startswith(\"//\")))\nexcept Exception:\n    d = []\nif isinstance(d, dict):\n    d = d.get(\"imports\") or d.get(\"installed\") or d.get(\"installedPlugins\") or []\ncwd = os.path.realpath(os.getcwd())\ntop = cwd\nwhile not os.path.exists(j(top, \".git\")) and os.path.dirname(top) != top:\n    top = os.path.dirname(top)\nhere = {cwd, top if os.path.exists(j(top, \".git\")) else cwd}\nrank = {\"managed\": 4, \"local\": 3, \"project\": 2}\nlevels = {}\nfor p in d if isinstance(d, list) else []:\n    if not isinstance(p, dict) or p.get(\"enabled\") is False or p.get(\"installed\") is False:\n        continue\n    if (p.get(\"name\") or str(p.get(\"id\") or \"\").split(\"@\")[0]) != \"marc\":\n        continue\n    scope, pp = p.get(\"scope\"), p.get(\"projectPath\")\n    if pp or scope in (\"local\", \"project\"):\n        if not isinstance(pp, str) or not os.path.isabs(pp) or os.path.realpath(pp) not in here:\n            continue\n    r = p[\"path\"] if \"path\" in p else p.get(\"installPath\", p.get(\"cache_path\"))\n    m, v = p.get(\"marketplaceName\"), p.get(\"version\")\n    if r is None and all(isinstance(x, str) and x and x.strip(\".\") and \"/\" not in x for x in (m, v)):\n        r = j(os.environ.get(\"CODEX_HOME\") or os.path.expanduser(\"~/.codex\"), \"plugins\", \"cache\", m, \"marc\", v)\n    if isinstance(r, str) and os.path.isabs(r) and os.path.isdir(j(r, \"scripts\")):\n        levels.setdefault(rank.get(scope, 2 if pp else 1), set()).add(os.path.realpath(r))\nroots = levels[max(levels)] if levels else set()\nif len(roots) != 1:\n    why = \"ambiguous: several enabled marc plugin roots at the same scope\" if roots else \"no installed marc plugin root\"\n    sys.stderr.write(\"[mARC] \" + why + \" in the plugin list; refusing to fall back to the workspace\\n\")\nprint(j(roots.pop(), \"scripts\") if len(roots) == 1 else \"/marc-plugin-root-unresolved\")\n")')"
python3 "$S/release_verify.py" --json
```
Defaults to `plugin.json`'s version. Non-zero exit = NOT fully verified — read
which check failed before reporting shipped.

**Merge handoff requires the proof, not the assertion** — pass the verifiable
`@sec` record (the `## @sec review` comment URL), never a bare "APPROVED" from
memory. This repo's PR author can't self-approve, so `reviewDecision` is
always empty; that's expected, don't re-block on it. (origin: #105 · 2026-07-16)

**Merge gate: one call, run at every push and before any merge.**
```bash
python3 "$S/merge_gate.py" --pr <N> --expect-reviewer sec=<id> --expect-reviewer rev=<id>
```
Exit `0` passes, `1` blocked (each blocker printed with its fix), `2` PR
unreadable (never a pass). `--json` for the full record.

<!-- rules:origin-required -->
- **Autonomously dispatch pre-merge gates immediately upon PR open.** Dispatch
  `@sec` and `@rev` in the background at once; don't ask permission. Done means
  merged and validated. (origin: #243 · 2026-09-08)
- **The pre-merge gate is `@sec` AND `@rev` AND bots-adjudicated-at-HEAD;
  `merge_gate.py` enforces it.** Both `## @sec review` / `## @rev review`
  markers, from a trusted author (association missing/untrusted fails closed),
  with `reviewed-sha:` equal to the current HEAD and a verdict; BLOCK from
  either blocks. Trust boundary: the association check filters
  non-collaborator forgeries, it does not authenticate the reviewer. Mint a
  unique `reviewer: <harness>/<dispatch-id>` per dispatch and pass it as
  `--expect-reviewer` (SHOULD: audit traceability, not authentication).
  Unresolved inline review threads block too ("CI green" is not permission):
  per thread, verify the fix, reply citing the commit (or a stated won't-fix
  justification), resolve; repeat at every push.
  (origin: #125 · 2026-07-16) (origin: #139 · 2026-07-20) (origin: #216 · 2026-10-03)
- **Re-read the operating-invariants card before tagging or merging.** Treat
  `invariants-card.md` as a checkpoint at that
  moment, not just a post-compaction reminder. (origin: #41 · 2026-07-21)
<!-- /rules:origin-required -->

**Terminal-state playbook: `REVIEW_REQUIRED` with no eligible non-author
approver** (`merge_gate.py` reports it as a blocker; no push fixes it).
<!-- rules:origin-required -->
- **Detect this early, not at merge time:** run the gate right after opening
  the PR or dispatching reviews. (origin: #133 · 2026-07-20)
- **Escalate with a named ask, never a vague "blocked":** request a specific
  human (`gh pr edit <N> --add-reviewer <user>`) or state the exact action, and
  set the board item to **Blocked** with that ask. (origin: #133 · 2026-07-20)
- **`--admin` override policy.** `gh pr merge --admin` needs explicit,
  in-the-moment human authorization for this PR, never a standing default;
  record who authorized it and why. (origin: #133 · 2026-07-20)
<!-- /rules:origin-required -->
