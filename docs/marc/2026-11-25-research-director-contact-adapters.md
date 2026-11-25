# Research brief: contact adapters for the engineering director (#433)

Source: [issue #433 brief comment](https://github.com/NexaDuo/mARC/issues/433#issuecomment-7175743280), posted by `@research`. Design decision: [issue #433 design comment](https://github.com/NexaDuo/mARC/issues/433#issuecomment-7175762767).

**Question:** per harness, how does one process or session deliver a message to a running or idle agent session, and how is a saved session resumed? Which harness-neutral fallbacks exist? (#433, extends #411)

**TL;DR** — Only Claude Code has a documented, local, process-to-process live-delivery path (cross-session messaging via `ListAgents`/`SendMessage`). It works with no Remote Control on either side. Remote Control is for phone/web and for other machines. Codex, Copilot CLI and Antigravity have documented resume and documented human remote-control surfaces, but I found no documented peer-to-peer "message another local session" call. So for those three, the safe order is resume-after-exit plus an inbox, with their protocol servers (app-server, ACP) as optional, unproven-for-this-use extras. Multiplexers are a real but unsafe optional last resort. Confidence: high for Claude Code, medium for the rest. Option A is verified only for Claude Code; B is the verified floor everywhere.

### Per-harness table
| Harness | Live delivery to running/idle session | Resume after exit | Headless limits | Status | Source |
|---|---|---|---|---|---|
| Claude Code | `SendMessage` to a name found by `ListAgents`. Running session reads it between tool calls; idle session gets a new turn. Local delivery is over a per-session Unix socket, never Anthropic servers. Needs v2.1.224+ (Linux/macOS/WSL2). Remote Control is needed only to reach other machines/cloud. | `--resume`/`-r` (ID or name), `--continue`/`-c`, `--fork-session`, `--session-id`; `--bg` background sessions | `-p` binds the inbox socket too, so a long `-p` worker can receive messages and appears in the listing. Not in bare mode. Held messages in `-p` expire after 5 min (`dialogExpiry`) unless `crossSessionInbound: accept` is set | verified | [cross-session](https://code.claude.com/docs/en/cross-session-messaging), [CLI ref](https://code.claude.com/docs/en/cli-reference), local `--help` |
| Codex CLI | No documented peer-message call. `app-server` (experimental; stdio, WebSocket or Unix socket) and `remote-control` exist; `--remote` connects a TUI to an app-server | `codex exec resume [ID] [PROMPT]`, `--last`; `codex resume` | `exec` is one-shot. Whether a second process can inject into a live thread via app-server: not shown | resume verified; live path inferred | [Codex CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli), local `--help` |
| Copilot CLI | `--remote`: you can send prompts to a running session from GitHub.com/Mobile, same account only. No local peer path found. `--acp`: stdio (one client) or `--port` TCP on 127.0.0.1 (multiple connections) | `--resume [ID]`, `--continue`; resuming re-enables remote control | Remote control "does not work with programmatic use of `--prompt`". `--connect` (needs cloud sessions) only listed in passing | resume + remote verified; ACP-as-inbox inferred | [about remote control](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/about-remote-control), [ACP server](https://docs.github.com/en/copilot/reference/copilot-cli-reference/acp-server) |
| Antigravity `agy` | Remote Control is for the web dashboard, same Google account; `agy remote-control start/status/stop` is an always-on OS-service daemon. `agy acp` serves editors. No peer-message call found | `--continue`/`-c` (per workspace, from a cache file), `--conversation <id>` | `-p` documented on a separate headless page I did not read | resume + remote verified; rest inferred | [Remote Control](https://antigravity.google/docs/remote-control.md), [resume](https://antigravity.google/docs/cli/commands/resume) |

Key quotes:
- Claude: "The receiving Claude reads the message between tool calls during an active turn. When the receiving session is idle, Claude Code starts a new turn with the message." Also: "A session appears only when it binds an inbox socket." And "Claude Code binds an inbox socket for a `claude -p` session like an interactive one, so a long-running `-p` worker can receive messages."
- Claude, Remote Control: "To receive a reply from a session on another of your machines, connect your session to Remote Control before Claude sends." For the same machine, delivery is "over a per-session socket ... never through Anthropic servers", so neither side needs Remote Control locally.
- Copilot: "Only the GitHub account that started the session can control it." ACP stdio: "Only one client is supported."
- Codex: "Resume an exec session by ID or add `--last` to continue the most recent session from the current working directory." `remote-control`: "Run or manage remote control for the local app-server."
- Antigravity: "Interactive tunnels (`/remote-control` or `agy --remote-control`) close when the terminal session exits."

Notes
- Claude Code safety controls (verified): messages arrive as plain text, "can't approve anything", can't change config, and permission prompts still fire. If the receiver bypasses permissions, the default is to hold messages for human approval unless the sender also bypasses. Held messages need a human or `crossSessionInbound: accept` (set via `--settings`). A session inside a container and one on the host can't reach each other. Loops can run unbounded if slow. Same-machine messaging is per OS user.
- The ListAgents/SendMessage name addressing is by session name (`--name`/`/rename`). `--remote-control` itself is not required locally. [inference] The director should start techleads with `--name` so they are addressable.
- Codex as an MCP server (`codex mcp-server`): the MCP docs page I read covers only Codex as an MCP client. Treat as undocumented here.
- Copilot `--connect`, Codex app-server live injection, Antigravity ACP session continuation: insufficient public evidence.

### Harness-neutral fallbacks
- **Tracker comment inbox**: durable and auditable, but it cannot wake an idle session; something must poll or a human must prompt. [inference]
- **File inbox + watcher**: a monitor can wake a session only where the harness supports background watchers. Otherwise it is poll-on-turn. [inference] Claude Code also documents `CLAUDE_CODE_MESSAGING_SOCKET`, so a hook can post into its own session (verified); posting into another session's socket is not documented as supported, so do not rely on it.
- **tmux `send-keys`**: `-l` sends literal characters. Targets are `session:window.pane` or a `%id`. Unqualified targets are "guessed"; multiple name matches error; an omitted pane index means the "currently active pane". [tmux(1)](https://man.archlinux.org/man/tmux.1) (verified)
- **zellij `action write-chars` / `write`**: `-p/--pane-id` targets a pane (`ZELLIJ_PANE_ID` holds the own id); "If you omit `--pane-id`, ... the currently focused pane." [zellij CLI actions](https://zellij.dev/documentation/cli-actions.html) (verified)
- **wezterm `cli send-text`**: `--pane-id`; default is the pane from `WEZTERM_PANE`; sent as bracketed paste unless `--no-paste`. [wezterm send-text](https://wezterm.org/cli/cli/send-text.html) (verified)
- **Safety caveats** [inference from the above]: a pane may be at a shell prompt (typed text runs as a command), at a permission dialog (a keystroke could approve it), or may not be the techlead at all. Defaults to "focused" or "active" pane make wrong-target the common failure. Always pass an explicit pane id captured at discovery, verify identity first, and never send into a pane showing a prompt you can't classify.

### Implications for the decision
- **A**: valid as written only for Claude Code (verified). Remote Control is not the local mechanism; cross-session messaging is. The skill should say "Remote Control" only for cross-machine reach. Labels for other harnesses must read "resume + inbox" until a native path is documented.
- **B**: the verified floor on all four harnesses.
- **C**: all three multiplexers verifiably support explicit pane targeting, but they carry the unsafe-injection risk above. Keep optional, discovered, explicit-id only, and never before the native path or resume.
- Recommended order:
  - Claude Code: `SendMessage` (named techlead) → `--resume <name|id>` (`-p` for unattended) → file/tracker inbox → optional multiplexer.
  - Codex: `codex exec resume <id> "<prompt>"` (or `-` for stdin) → inbox → optional app-server/multiplexer. [live path inferred]
  - Copilot: `--resume <id>`/`--continue` → inbox → optional `--acp --port` or multiplexer. `--remote` is human-only.
  - Antigravity: `agy --conversation <id>` (`-c` is workspace-scoped and cache-dependent) → inbox → optional multiplexer.
- Resuming while the original process is still alive is not covered by any doc I read; the skill should only resume after exit. [gap]

### Coverage & gaps
- Searched: cross-session messaging, Copilot CLI remote/ACP, Antigravity remote/resume/ACP, Codex reference/MCP, tmux/zellij/wezterm manuals.
- Read: about 12 official pages (several truncated; Copilot and Codex reference pages were read only in part, first 100k chars). Plus local `--help` facts supplied by the operator.
- NOT found: any documented peer-to-peer message call for Codex, Copilot or Antigravity; Codex `mcp-server` docs; Copilot `--connect` semantics; Antigravity headless `-p` and ACP continuation details; whether `--input-format stream-json` accepts mid-run input in Claude `-p` (only the flag listing was seen).
- Staleness/bias: all sources are vendor docs (current, 2026-11), no independent measurements. Codex docs moved domains (redirects). ACP in Copilot is public preview. The Claude cross-session feature is recent (v2.1.224+), so version-gate it.

