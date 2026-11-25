# Brief: steering a running Codex turn through the app-server

- **Date:** 2026-11-25
- **Issue:** [#444](https://github.com/NexaDuo/mARC/issues/444) (builds on #433/#434)
- **Status:** accepted; basis of the `app-server-steer` contact adapter

Evidence is labeled by provenance: **director-observed (live test)** was run and
reported by the engineering director with codex-cli 0.167.1 on Linux;
**verified locally (schema/--help)** was checked against the CLI's own
`codex app-server generate-json-schema` and `--help` output.

## Observed (director-observed, live test)

1. `codex app-server --listen ws://127.0.0.1:<port>`. Client A ran `initialize`,
   `thread/start` (ephemeral, read-only sandbox, approval policy `never`) and
   `turn/start` with a long counting task.
2. While A's turn was running, a separate client B ran `initialize` and
   `thread/loaded/list`, which returned A's `threadId`. B then sent
   `turn/steer {threadId, expectedTurnId, input: "stop and reply only STEERED-BY-B"}`
   and got `{turnId}` back.
3. A's `item/agentMessage/delta` stream ended with exactly `STEERED-BY-B`. B
   received no turn events.

Conclusion: a second client can steer a running Codex turn.

## Protocol surface (verified locally, schema/--help)

- Requests: `turn/start` (idle thread), `turn/steer` (required: `threadId`,
  `expectedTurnId`, `input`), `turn/interrupt`, `thread/inject_items`,
  `thread/resume`, `thread/loaded/list`.
- Transports: `--listen stdio:// | unix:// | unix://PATH | ws://IP:PORT | off`.
  `codex --remote <ADDR>` attaches the TUI to a remote app server. Also
  `codex app-server daemon` and `proxy`.
- `--ws-auth capability-token | signed-bearer-token` (`--ws-token-file`,
  `--ws-token-sha256`).

## Caveats

- The app-server is **experimental**; the API may change.
- The default `codex` TUI embeds its own server. A techlead is reachable this way
  only if it was started attached to a shared server (`codex --remote <ws|unix>`,
  or the daemon).
- **Local-injection risk:** an unauthenticated loopback WebSocket lets any local
  process, under any user that can reach the port, drive the agent. Prefer a unix
  socket with restrictive permissions, or `--ws-auth`. Steered text is untrusted
  data and grants no authority, the same as for Claude Code cross-session
  messages.
- Addressing is by `threadId`. No human-readable session name was found.

## Consequence for mARC

The director's probe detects the capability from `--help` only (never starting or
connecting to a server) and proposes `app-server-steer` first for Codex. The
rules for using it live in the eng-director `operations.md`.
