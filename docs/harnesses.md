# Harnesses and compatibility

How mARC compiles one core team into per-harness plugins, and what each harness supports.

Back to the [README](../README.md).

## Harness architecture

mARC packages a full software-delivery agent team, dressed in a retro/vaporwave **IRC** console aesthetic. Rather than building separate agent systems for every platform, mARC defines a single core team in `core/` and compiles it into plugins for different developer tools, which we call **harnesses**. Today, mARC ships four harnesses: Claude Code, OpenAI Codex, Google Antigravity, and GitHub Copilot CLI. It relies on the host tool to run and isolate subagents, providing a consistent team that you install once and reuse across your projects.

To support multiple host tools, mARC uses a harness-based architecture. A single core team of agents is defined under `core/` and compiled into platform-specific plugins located in the `harnesses/` directory.

Currently, mARC ships four harnesses: Claude Code (plugin under `harnesses/claude-code/marc/`), OpenAI Codex (plugin under `harnesses/codex/marc/`), Google Antigravity (plugin under `harnesses/antigravity/marc/`), and GitHub Copilot CLI (plugin under `harnesses/copilot/marc/`).

Because each host tool provides different agent execution APIs, the subagent dispatch mechanism varies between harnesses. Claude Code uses the native `Agent` tool to dispatch subagents, Codex uses its native agent dispatch, Google Antigravity uses `invoke_subagent`, and Copilot uses the `task` tool with plugin custom agents (`marc:*`).

The leader skill `@techlead` dynamically inspects the available tools and maps its requests to the appropriate dispatch tool. For detailed mapping notes, refer to the trackers below.

## Compatibility trackers

- [Google Antigravity compatibility tracker](../harnesses/antigravity/marc/COMPATIBILITY.md)
- [GitHub Copilot compatibility tracker](../harnesses/copilot/marc/COMPATIBILITY.md)

## Claude Code

Plugin under `harnesses/claude-code/marc/`. Subagents are dispatched with the native `Agent` tool.

## OpenAI Codex

Initial support is verified with CLI 0.156.1 for marketplace installation and
bundled-helper execution from a fresh consumer without `team.toml`. Writers use
`codex exec --worktree --sandbox workspace-write`. All read-only roles route to
Claude Code and fail closed if unavailable: a filesystem sandbox alone does not
prove tool restrictions. Native agent TOMLs are templates; automatic plugin-agent
discovery is not yet verified. Startup commands are compiled, but real hook
trust/event delivery still needs validation. Codex `read-guard` is disabled and
telemetry/sentinel tools report **unavailable** without reading transcripts or
writing telemetry. To intentionally process Claude data, pass
`--harness claude-code`. Detailed tech-lead procedures load on demand from
references to keep the initial skill prompt small.

## Google Antigravity

Plugin under `harnesses/antigravity/marc/`. Subagents are dispatched with `invoke_subagent`. See the [compatibility tracker](../harnesses/antigravity/marc/COMPATIBILITY.md).

## GitHub Copilot CLI

Plugin under `harnesses/copilot/marc/`. Subagents are dispatched with the `task` tool and plugin custom agents (`marc:*`). See the [compatibility tracker](../harnesses/copilot/marc/COMPATIBILITY.md).
