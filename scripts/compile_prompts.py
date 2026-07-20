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

    print("\nPrompt compilation complete.")

if __name__ == "__main__":
    main()
