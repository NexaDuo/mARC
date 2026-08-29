#!/usr/bin/env python3
import json
import os
import shutil
import sys

def compile_file(source_path, dest_path, config):
    with open(source_path, "r", encoding="utf-8") as sf:
        content = sf.read()

    # Replace all {{ key }} placeholders
    for key, value in config.items():
        placeholder = f"{{{{ {key} }}}}"
        content = content.replace(placeholder, str(value))

    # Ensure output directory exists
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    with open(dest_path, "w", encoding="utf-8") as df:
        df.write(content)
    print(f"Compiled: {source_path} -> {dest_path}")

def sync_scripts(core_scripts_dir, dest_scripts_dir):
    """Mirror core/scripts/ into a harness's marc/scripts/ verbatim (no
    templating — unlike the .md prose, scripts are byte-identical across
    harnesses, origin: #128). Idempotent: removes stale files in the
    destination that no longer exist in core/scripts/ (ignoring
    __pycache__), then copies every source file byte-for-byte, preserving
    mode (executable bit) and mtime via shutil.copy2."""
    if not os.path.isdir(core_scripts_dir):
        return

    source_names = {
        f for f in os.listdir(core_scripts_dir)
        if os.path.isfile(os.path.join(core_scripts_dir, f))
    }

    os.makedirs(dest_scripts_dir, exist_ok=True)

    # Remove stale files (present in dest, absent from source), ignoring
    # generated artifacts like __pycache__.
    for existing in os.listdir(dest_scripts_dir):
        if existing == "__pycache__":
            continue
        existing_path = os.path.join(dest_scripts_dir, existing)
        if existing not in source_names and os.path.isfile(existing_path):
            os.remove(existing_path)
            print(f"Removed stale script: {existing_path}")

    for name in sorted(source_names):
        source_file = os.path.join(core_scripts_dir, name)
        dest_file = os.path.join(dest_scripts_dir, name)
        shutil.copy2(source_file, dest_file)
        print(f"Copied script: {source_file} -> {dest_file}")

# Characters that would let a spec/config value break out of the shell
# contexts these values are interpolated into (bash variable *names* in
# `${{{...}}}`, and path segments inside an already-double-quoted string).
# Rejecting these outright (fail-closed) rather than `shlex.quote()`-ing them
# is deliberate: quoting a bash variable *name* is a syntax error, not a fix,
# and quoting a path segment that's already inside a double-quoted string
# just relocates the metacharacter rather than neutralizing it. Every value
# reaching a hook command comes from `core/hooks/hooks.spec.json` or a
# harness's `compile.json` — repo-controlled today, but this is the sole
# compiler for the shipped `hooks.json`, so it must refuse to emit broken or
# splice-able shell rather than rely on reviewer discipline over raw JSON
# diffs (origin: #173, flagged by `@sec` on PR #189).
_UNSAFE_SHELL_CHARS = set('"\'`$;&|<>\n\r\\')


def _assert_shell_safe(value, context):
    value = str(value)
    bad = _UNSAFE_SHELL_CHARS & set(value)
    if bad:
        raise ValueError(
            f"unsafe shell metacharacter(s) {sorted(bad)!r} found in {context} ({value!r}) "
            "— refusing to compile a hooks.json that splices this into a shell command"
        )


def _substitute(text, config):
    for key, value in config.items():
        placeholder = f"{{{{ {key} }}}}"
        if placeholder in text:
            _assert_shell_safe(value, f"compile.json field {key!r}")
            text = text.replace(placeholder, str(value))
    return text


# `sed` pattern that pulls the `session_id` or `conversationId` field out of a hook's stdin JSON
# payload without a `jq` dependency (single quotes below are the shell's, so
# the literal double quotes inside don't need JSON/shell escaping here).
_SESSION_ID_SED = (
    "-e 's/.*\"session_id\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p' "
    "-e 's/.*\"conversationId\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p'"
)


def _report_once_fragment(marker_key, message_cmd):
    """Shell fragment: run `message_cmd` (a full statement, semicolon-
    terminated) at most once per session instead of once per invocation.

    `token-guard`/`outdated-recheck` are `PostToolUse` hooks, so a genuinely
    broken install (missing script) previously repeated its stderr line on
    every single tool call — flooding the transcript and burning context on
    exactly the kind of diagnostic that's supposed to be cheap (origin: #170
    AC#2, dedup gap flagged by `@rev` on PR #189). This stays visible (never
    silent — that was the #170 bug) but reports once per session by keying a
    state-file marker off the hook's own stdin `session_id` or `conversationId`
    (falling back to ANTIGRAVITY_CONVERSATION_ID, then PPID — still reported
    at least once per session, never permanently silent across new sessions,
    origin: #221). `marker_key` must already be shell-safe (validated via
    `_assert_shell_safe`, or a literal drawn from one)."""
    return (
        'st="${MARC_STATE_DIR:-$HOME/.claude/marc-state}/hook-missing"; '
        'mkdir -p "$st" 2>/dev/null; '
        f'sid="$(cat 2>/dev/null | LC_ALL=C sed -n {_SESSION_ID_SED} | head -n1)"; '
        'sid="${sid:-${ANTIGRAVITY_CONVERSATION_ID:-$PPID}}"; '
        'mk="$st/$(printf \'%s\' "${sid:-nosession}_' + marker_key + '" '
        '| LC_ALL=C tr -c \'A-Za-z0-9_.-\' \'_\')"; '
        'if [ ! -f "$mk" ]; then ' + message_cmd + ' touch "$mk" 2>/dev/null; fi'
    )


def _script_hook_command(hook, config):
    """Render a 'kind': 'script' hook entry into a shell command that:
      * resolves the script under THIS harness's own plugin_root_env (fixing
        the #170 root cause: no more hardcoded CLAUDE_PLUGIN_ROOT shared
        across harnesses via a symlink),
      * translates that value into CLAUDE_PLUGIN_ROOT/CLAUDE_PROJECT_DIR
        (exported, harness-local) when the harness declares different env var
        names, so the shipped .sh scripts (which read those two names) work
        unmodified on every harness — one script body, every harness,
      * and — the #170 AC#2 fix — reports ONCE, visibly, on stderr, if the
        script itself cannot be found, instead of the old blanket
        `2>/dev/null; exit 0` that made a resolution failure indistinguishable
        from "ran, nothing to report". A hook's own internal quiet no-ops
        (e.g. "no team.toml in this repo") are untouched by this — they still
        go through the script's own `2>/dev/null` and stay quiet.
    """
    plugin_root_env = config["plugin_root_env"]
    project_dir_env = config["project_dir_env"]
    script_name = hook["script"]

    _assert_shell_safe(plugin_root_env, "compile.json field 'plugin_root_env'")
    _assert_shell_safe(project_dir_env, "compile.json field 'project_dir_env'")
    _assert_shell_safe(script_name, f"hooks.spec.json hook {hook.get('id')!r} field 'script'")

    is_native = plugin_root_env == "CLAUDE_PLUGIN_ROOT" and project_dir_env == "CLAUDE_PROJECT_DIR"
    prefix = ""
    if not is_native:
        prefix = (
            f'export CLAUDE_PLUGIN_ROOT="${{{plugin_root_env}:-$PWD}}"; '
            f'export CLAUDE_PROJECT_DIR="${{{project_dir_env}:-${{OLDPWD:-$PWD}}}}"; '
        )

    not_found_msg = (
        f'echo "[mARC] hook script not found: $script (is {plugin_root_env} set for this harness?)" >&2; '
    )
    not_found_branch = _report_once_fragment(script_name, not_found_msg)

    return (
        f'{prefix}script="${{{plugin_root_env}:-$PWD}}/hooks/{script_name}"; '
        'if [ -f "$script" ]; then bash "$script" 2>/dev/null; '
        f'else {not_found_branch}; fi; '
        'exit 0'
    )


def _build_command(hook, config):
    if hook["kind"] == "inline":
        return _substitute(hook["command"], config)
    if hook["kind"] == "script":
        return _script_hook_command(hook, config)
    raise ValueError(f"unknown hook kind: {hook['kind']!r} (id={hook.get('id')!r})")


_CC_EVENT_MAP = {
    "session_start": "SessionStart",
    "session_start_compact": "SessionStart",
    "post_tool_use": "PostToolUse",
    "stop": "Stop",
}
_CC_MATCHER_MAP = {
    "session_start": "*",
    "session_start_compact": "compact",
    "post_tool_use": "*",
    "stop": "*",
}
_COPILOT_EVENT_MAP = {
    "session_start": "sessionStart",
    "post_tool_use": "postToolUse",
}
_AGY_EVENT_MAP = {
    "session_start": "PreInvocation",
    "session_start_compact": "PreInvocation",
    "pre_invocation": "PreInvocation",
    "post_invocation": "PostInvocation",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "stop": "Stop",
}


def render_claude_code_hooks(selected_hooks, config):
    """Claude Code dialect: {"hooks": {EventName: [{"matcher", "hooks":
    [{"type": "command", "command": ...}]}]}}."""
    out = {"hooks": {}}
    for hook in selected_hooks:
        event_name = _CC_EVENT_MAP[hook["event"]]
        matcher = _CC_MATCHER_MAP[hook["event"]]
        command = _build_command(hook, config)
        out["hooks"].setdefault(event_name, []).append({
            "matcher": matcher,
            "hooks": [{"type": "command", "command": command}],
        })
    return out


def render_copilot_hooks(selected_hooks, config):
    """Copilot dialect: {"version": 1, "hooks": {eventName: [{"type":
    "command", "timeoutSec": N, "bash": ...}]}} — a structurally different
    schema from Claude Code's (flat entries, no matcher, lowerCamel event
    names), so it gets its own renderer rather than forcing one template over
    both shapes."""
    out = {"version": 1, "hooks": {}}
    for hook in selected_hooks:
        event_name = _COPILOT_EVENT_MAP[hook["event"]]
        command = _build_command(hook, config)
        out["hooks"].setdefault(event_name, []).append({
            "type": "command",
            "timeoutSec": hook.get("copilot_timeout_sec", 10),
            "bash": command,
        })
    return out


def render_antigravity_hooks(selected_hooks, config):
    """Antigravity dialect: top-level map of hook-id -> event map.
    PreInvocation, PostInvocation, Stop use a flat list of handler objects:
      {"<hook_id>": {"PreInvocation": [{"type": "command", "command": ...}]}}
    PreToolUse, PostToolUse use a grouped list with matcher:
      {"<hook_id>": {"PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": ...}]}]}}
    """
    out = {}
    for hook in selected_hooks:
        hook_id = hook["id"]
        event_name = _AGY_EVENT_MAP[hook["event"]]
        command = _build_command(hook, config)
        handler = {"type": "command", "command": command}
        if event_name in ("PreToolUse", "PostToolUse"):
            event_val = [{
                "matcher": "*",
                "hooks": [handler],
            }]
        else:
            event_val = [handler]

        out.setdefault(hook_id, {})[event_name] = event_val
    return out


_HOOK_DIALECT_RENDERERS = {
    "antigravity": render_antigravity_hooks,
    "claude-code": render_claude_code_hooks,
    "copilot": render_copilot_hooks,
}


def get_hooks_json_path(harness_marc_path, config):
    """Return the destination path for hooks.json for the given harness config.
    Antigravity emits hooks.json at the plugin root; Claude Code and Copilot emit to hooks/hooks.json."""
    hooks_path = config.get("hooks_path")
    if hooks_path:
        return os.path.join(harness_marc_path, hooks_path)
    dialect = config.get("hook_dialect")
    if dialect == "antigravity":
        return os.path.join(harness_marc_path, "hooks.json")
    return os.path.join(harness_marc_path, "hooks", "hooks.json")


def compile_hooks(core_dir, harness_marc_path, config):
    """Compile core/hooks/hooks.spec.json into this harness's native
    hooks.json (at plugin root for Antigravity, or hooks/hooks.json for Claude
    Code / Copilot) + the .sh scripts it actually references (origin: #173,
    #170, #197). A harness with no 'hook_dialect' in its compile.json is left
    alone (no hooks/ directory is asserted or written) — hooks are opt-in per
    harness via that explicit capability declaration, not an accident of
    what a prior hand-edit happened to include."""
    dialect = config.get("hook_dialect")
    if not dialect:
        return

    renderer = _HOOK_DIALECT_RENDERERS.get(dialect)
    if renderer is None:
        raise ValueError(f"unknown hook_dialect: {dialect!r} in {harness_marc_path}")

    spec_path = os.path.join(core_dir, "hooks", "hooks.spec.json")
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    hook_ids = set(config.get("hook_ids", []))
    # Preserve the spec's own declared order (single source of truth for
    # ordering), not whatever order hook_ids happens to be listed in.
    selected_hooks = [h for h in spec["hooks"] if h["id"] in hook_ids]

    rendered = renderer(selected_hooks, config)

    dest_hooks_dir = os.path.join(harness_marc_path, "hooks")
    # Replace a committed symlink (e.g. the old Antigravity `hooks -> ../../
    # claude-code/marc/hooks`, origin #170) with a real, self-contained,
    # compiled directory.
    if os.path.islink(dest_hooks_dir):
        os.unlink(dest_hooks_dir)
    os.makedirs(dest_hooks_dir, exist_ok=True)

    dest_hooks_json = get_hooks_json_path(harness_marc_path, config)

    # Clean up any stale hooks.json in alternative locations
    subfolder_hooks_json = os.path.join(dest_hooks_dir, "hooks.json")
    root_hooks_json = os.path.join(harness_marc_path, "hooks.json")

    if dest_hooks_json != subfolder_hooks_json and os.path.isfile(subfolder_hooks_json):
        os.remove(subfolder_hooks_json)
        print(f"Removed stale hooks/hooks.json: {subfolder_hooks_json}")

    if dest_hooks_json != root_hooks_json and os.path.isfile(root_hooks_json):
        os.remove(root_hooks_json)
        print(f"Removed stale root hooks.json: {root_hooks_json}")

    # Only ship the .sh scripts this harness's OWN selected hooks actually
    # reference (a harness that declares no "script"-kind hooks, e.g.
    # Copilot today, ships none — do not silently expand its coverage by
    # dropping unused scripts on it).
    needed_scripts = {h["script"] for h in selected_hooks if h["kind"] == "script"}
    core_hooks_dir = os.path.join(core_dir, "hooks")

    for existing in os.listdir(dest_hooks_dir):
        existing_path = os.path.join(dest_hooks_dir, existing)
        if existing == "hooks.json":
            continue
        if existing == "lib":
            continue
        if os.path.isfile(existing_path) and existing not in needed_scripts:
            os.remove(existing_path)
            print(f"Removed stale hook script: {existing_path}")

    for name in sorted(needed_scripts):
        src = os.path.join(core_hooks_dir, name)
        dst = os.path.join(dest_hooks_dir, name)
        shutil.copy2(src, dst)
        print(f"Copied hook script: {src} -> {dst}")

    # lib/ (shared version-check helper) is needed only when at least one of
    # outdated-check.sh/outdated-recheck.sh is shipped.
    lib_needed = bool({"outdated-check.sh", "outdated-recheck.sh"} & needed_scripts)
    dest_lib_dir = os.path.join(dest_hooks_dir, "lib")
    if lib_needed:
        core_lib_dir = os.path.join(core_hooks_dir, "lib")
        os.makedirs(dest_lib_dir, exist_ok=True)
        lib_names = {f for f in os.listdir(core_lib_dir) if os.path.isfile(os.path.join(core_lib_dir, f))}
        for existing in os.listdir(dest_lib_dir):
            if existing not in lib_names and os.path.isfile(os.path.join(dest_lib_dir, existing)):
                os.remove(os.path.join(dest_lib_dir, existing))
        for name in sorted(lib_names):
            shutil.copy2(os.path.join(core_lib_dir, name), os.path.join(dest_lib_dir, name))
    elif os.path.isdir(dest_lib_dir):
        shutil.rmtree(dest_lib_dir)
        print(f"Removed stale hooks/lib/: {dest_lib_dir}")

    with open(dest_hooks_json, "w", encoding="utf-8") as f:
        json.dump(rendered, f, indent=2)
        f.write("\n")
    print(f"Compiled hooks: {spec_path} -> {dest_hooks_json}")


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    core_dir = os.path.join(base_dir, "core")
    harnesses_dir = os.path.join(base_dir, "harnesses")

    if not os.path.exists(core_dir):
        print(f"Error: core/ directory not found at {core_dir}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(harnesses_dir):
        print(f"Error: harnesses/ directory not found at {harnesses_dir}", file=sys.stderr)
        sys.exit(1)

    # Walk through each harness folder
    for harness in sorted(os.listdir(harnesses_dir)):
        harness_marc_path = os.path.join(harnesses_dir, harness, "marc")
        compile_config_path = os.path.join(harness_marc_path, "compile.json")

        if not os.path.exists(compile_config_path):
            continue

        print(f"\n--- Loading config and compiling prompts for: {harness} ---")
        try:
            with open(compile_config_path, "r", encoding="utf-8") as cf:
                config = json.load(cf)
        except Exception as e:
            print(f"Error loading {compile_config_path}: {e}", file=sys.stderr)
            sys.exit(1)

        # Walk through the core/ template files
        for root, _, files in os.walk(core_dir):
            for file in files:
                if not file.endswith(".md"):
                    continue
                source_file = os.path.join(root, file)
                rel_path = os.path.relpath(source_file, core_dir)
                dest_file = os.path.join(harness_marc_path, rel_path)
                compile_file(source_file, dest_file, config)

        # Mirror core/scripts/ verbatim (byte-identical, no templating).
        core_scripts_dir = os.path.join(core_dir, "scripts")
        dest_scripts_dir = os.path.join(harness_marc_path, "scripts")
        sync_scripts(core_scripts_dir, dest_scripts_dir)

        # Compile core/hooks/hooks.spec.json into this harness's native
        # hooks/hooks.json (origin: #173, #170).
        compile_hooks(core_dir, harness_marc_path, config)

    print("\nPrompt compilation complete.")

if __name__ == "__main__":
    main()
