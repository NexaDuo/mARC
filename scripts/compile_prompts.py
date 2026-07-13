#!/usr/bin/env python3
import json
import os
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

    print("\nPrompt compilation complete.")

if __name__ == "__main__":
    main()
