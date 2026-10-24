#### Concurrent operators (claim before you dispatch)
Two `@techlead` operators (different harnesses, or two sessions) may share one
clone with no supervisor. There is no locking layer, by design. The protocol
is enforced by `board.py` (logic in `board_claim.py`); run it, don't re-derive
it. Run the line whole (`S=` must be set in the same shell call):
```bash
S="$(cat "${COPILOT_HOME:-$HOME/.copilot}/config.json" | python3 -I -c 'exec("import json, os, sys\nj = os.path.join\ntry:\n    d = json.loads(\"\\n\".join(x for x in sys.stdin.read().splitlines() if not x.lstrip().startswith(\"//\")))\nexcept Exception:\n    d = []\nif isinstance(d, dict):\n    d = d.get(\"imports\") or d.get(\"installed\") or d.get(\"installedPlugins\") or []\ncwd = os.path.realpath(os.getcwd())\ntop = cwd\nwhile not os.path.exists(j(top, \".git\")) and os.path.dirname(top) != top:\n    top = os.path.dirname(top)\nhere = {cwd, top if os.path.exists(j(top, \".git\")) else cwd}\nrank = {\"managed\": 4, \"local\": 3, \"project\": 2}\nlevels = {}\nfor p in d if isinstance(d, list) else []:\n    if not isinstance(p, dict) or p.get(\"enabled\") is False or p.get(\"installed\") is False:\n        continue\n    if (p.get(\"name\") or str(p.get(\"id\") or \"\").split(\"@\")[0]) != \"marc\":\n        continue\n    scope, pp = p.get(\"scope\"), p.get(\"projectPath\")\n    if pp or scope in (\"local\", \"project\"):\n        if not isinstance(pp, str) or not os.path.isabs(pp) or os.path.realpath(pp) not in here:\n            continue\n    r = p[\"path\"] if \"path\" in p else p.get(\"installPath\", p.get(\"cache_path\"))\n    m, v = p.get(\"marketplaceName\"), p.get(\"version\")\n    if r is None and all(isinstance(x, str) and x and x.strip(\".\") and \"/\" not in x for x in (m, v)):\n        r = j(os.environ.get(\"CODEX_HOME\") or os.path.expanduser(\"~/.codex\"), \"plugins\", \"cache\", m, \"marc\", v)\n    if isinstance(r, str) and os.path.isabs(r) and os.path.isdir(j(r, \"scripts\")):\n        levels.setdefault(rank.get(scope, 2 if pp else 1), set()).add(os.path.realpath(r))\nroots = levels[max(levels)] if levels else set()\nif len(roots) != 1:\n    why = \"ambiguous: several enabled marc plugin roots at the same scope\" if roots else \"no installed marc plugin root\"\n    sys.stderr.write(\"[mARC] \" + why + \" in the plugin list; refusing to fall back to the workspace\\n\")\nprint(j(roots.pop(), \"scripts\") if len(roots) == 1 else \"/marc-plugin-root-unresolved\")\n")')"; python3 "$S/board.py" <subcommand>
```
`<subcommand>`: `worktrees --branch <branch>` (before any mutating dispatch),
`claim --issue <N> --operator <harness>/<session-id>`, `claim-status --issue
<N> [--operator …]`, `withdraw --issue <N> --operator …`.
Exit codes: `0` you own it / branch free, `1` taken or lost (pick another
item), `2` error (stop, don't guess), `3` **escalate to the user**: a suspected
forged marker or a dead worktree, with the remedy printed. Add `--json` for
the full resolution (live/retired claims, suspected forgeries, reasons).
<!-- rules:origin-required -->
- **Claim with a `## @techlead claim` comment marker, never the assignee field.**
  The marker carries `operator:`, `issue: #<N>` and `claimed-at:`; an issue
  with no valid marker is not claimed, whoever is assigned. (origin: #213 · 2026-08-25)
- **A marker counts only from an `OWNER`/`MEMBER`/`COLLABORATOR` author**
  (public repo: anyone can comment). The script reads `authorAssociation` from
  `gh issue view --json comments` and `author_association` from REST, and fails
  closed on a missing value. Trust boundary: this coordinates cooperating
  operators, it is not authorization. (origin: #213 · 2026-08-25)
- **Supersedes #208's assignee-field claim:** a shared `gh` login makes every
  operator the same assignee, so the assignee is a human-visible label only and
  never evidence of operator identity. (origin: #213 · 2026-08-25)
- **The claim is knowingly racy; the tie-break is over trusted claims only.**
  No compare-and-swap exists on GitHub. The case-insensitively lowest
  `operator:` keeps the item; withdraw autonomously only when losing to a
  trusted, well-formed claim. A forged or malformed competitor never makes you
  stand down: surface it, leave the item pending. Never "both drop."
  (origin: #205 · 2026-08-25) (origin: #213 · 2026-08-25)
- **A `## @techlead withdraw` retires a claim only if trusted, posted after it,
  with the same `operator:`.** A withdraw failing that is noise, or a suspected
  forgery if it names a real claim. Resolve by the marker pair, never by a
  comment's presence or deletion. (origin: #213 · 2026-08-25) (origin: #216 · 2026-10-03)
- **Check `git worktree list` before every mutating dispatch.** A branch
  checked out elsewhere belongs to another operator: don't re-cut it or force a
  second checkout. (origin: #214 · 2026-08-25)
- **A dead worktree (locked or gone, at the base SHA, no commits, no linked PR)
  is neither a claim nor a squat.** Surface the printed remedy (`git worktree
  prune` / `git worktree remove --force <path>`) and let the user choose; never
  run it yourself. (origin: #214 · 2026-08-25)
- **Stale claims are reclaimed by a human, never by a timer.** A live trusted
  claim with no linked PR that never clears is a squat: escalate, don't steal.
  A forged claim is not a squat, and a bare human assignee is ordinary triage.
  (origin: #206 · 2026-08-25) (origin: #213 · 2026-08-25)
- **Isolation extends to the operators themselves:** each mutating operator
  takes its own worktree, never a shared branch or tree, and an in-repo
  worktree dir goes in the committed `.gitignore`, not `.git/info/exclude`.
  (origin: #206 · 2026-08-25) (origin: #214 · 2026-08-25)
<!-- /rules:origin-required -->
