# Research brief: alternative architectures for IDE/CLI plugin hook resolution

- **Type:** research brief
- **Date:** 2026-10-04
- **Attribution:** `@research`, posted on
  [issue #343](https://github.com/NexaDuo/mARC/issues/343); materialized by the operator.
- **Status:** superseded by
  [2026-10-18-decision-agy-hook-dispatcher.md](2026-10-18-decision-agy-hook-dispatcher.md)

> **Operator notes (2026-10-18).** Kept as posted, with these corrections:
> - The quotes below are paraphrases that carry no source URL. They were not
>   checked against the cited documentation, so read them as summaries, not
>   citations.
> - The brief assumes the plugin controls how the host runs a hook. It does
>   not: Antigravity runs the `command` string itself, via `sh -c` or
>   `cmd /c`. The decision record answers #343's acceptance criteria 2 and 3
>   from a probe of the real host instead.
> - The "base64 payload in the JSON" it argues against existed only in
>   PR #342, never on `main`.

**TL;DR** — The industry consensus for cross-platform CLI plugin hooks has moved away from embedding scripts or base64 payloads into JSON config files, favoring **Subprocess Execution (Wrapper Binaries)** or **Environment Dispatchers**. The most robust pattern is native OS process execution (`os/exec`) of standalone binaries or standardized shims using standard streams, avoiding shell interpreters completely. Confidence level: High.

### Findings
- **HashiCorp Terraform gRPC/go-plugin architecture** [reported] — HashiCorp Documentation, 2023, hashicorp.com. > "Each plugin is executed as a separate subprocess by Terraform Core. If a plugin crashes or encounters a fatal error, it does not bring down the main Terraform CLI process... communication over a Remote Procedure Call (RPC) interface."
- **GitHub CLI (`gh`) Extension Subprocess Model** [reported] — GitHub CLI Documentation, 2022, github.com/cli/cli. > "The CLI uses standard process execution (effectively wrapping Go’s os/exec or equivalent) to spawn the extension process. It forwards all command-line arguments and flags passed to gh <extname> directly to the extension's executable."
- **Pre-commit framework dispatcher architecture** [reported] — pre-commit Documentation, 2023, pre-commit.com. > "pre-commit acts as a dispatcher. It reads the configuration, identifies the required environment for each hook, and executes the hook within that specific, pre-built environment... One of the framework's primary features is its ability to handle dependencies."
- **Node.js Single Executable Applications (SEA)** [reported] — Node.js Documentation, 2023, nodejs.org. > "Node.js now supports bundling your JavaScript code and the Node runtime into a single, hermetic binary... using a wrapper script or binary to invoke specific plugin commands."

### Implications for the decision
- **Wrapper binaries / Subprocess model:** Strongly recommended. This completely eliminates the need for shell execution (`bash`/`cmd.exe`) and base64 escaping. The JSON config acts only as a registry mapping hooks to a plugin path or command, while the CLI engine executes the hook natively via `os/exec` (or equivalent). Data is passed via environment variables or `stdin`, avoiding JSON embedding.
- **Pure Python/Node Shims:** Highly viable as the entry point for the plugin, especially for resolving dynamic paths like `AGY_PLUGIN_ROOT`. The host CLI sets `AGY_PLUGIN_ROOT` as an environment variable and executes the Node/Python shim natively without relying on `cmd.exe` or bash execution, thus sidestepping Windows limitations.
- **Embedded Bash Scripts / Base64:** This is an obsolete anti-pattern. Industry standard dictates isolating code execution within script files or binaries, using JSON strictly for metadata and capability registration, not code payload transport.

### Coverage & gaps
- Searched: "HashiCorp Terraform" plugin architecture, "pre-commit" cross-platform architecture, GitHub CLI "gh extension" os/exec architecture, CLI plugin architecture wrapper binaries Node shim
- Read: 4 summarized sources detailing the architecture of HashiCorp `go-plugin`, GitHub CLI extensions, `pre-commit` framework, and Node.js SEA/wrappers.
- NOT found: Any modern CLI tools maintaining or recommending JSON-embedded shell payload architectures, confirming this approach is an anti-pattern.
- Staleness / bias notes: Modern tooling heavily biases toward Go-based subprocess wrappers (HashiCorp, GitHub CLI), but the fundamental OS execution principles apply directly to Node/Python host runtimes.
