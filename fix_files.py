import json

# 1. Fix docs/index.html
with open('docs/index.html', 'r') as f:
    content = f.read()
content = content.replace(
    'writes <code>.agents/team.toml</code> on Google Antigravity, or <code>.agents/team.toml</code> on Claude Code.',
    'writes <code>.agents/team.toml</code> on Google Antigravity, or <code>.claude/team.toml</code> on Claude Code.'
)
with open('docs/index.html', 'w') as f:
    f.write(content)

# 2. Fix harnesses/claude-code/marc/compile.json
with open('harnesses/claude-code/marc/compile.json', 'r') as f:
    data = json.load(f)
data['config_dir'] = '.claude'
data['agents_dir'] = '.agents'
with open('harnesses/claude-code/marc/compile.json', 'w') as f:
    json.dump(data, f, indent=2)

# 3. Fix harnesses/antigravity/marc/compile.json
with open('harnesses/antigravity/marc/compile.json', 'r') as f:
    data = json.load(f)
data['agents_dir'] = '.agents'
with open('harnesses/antigravity/marc/compile.json', 'w') as f:
    json.dump(data, f, indent=2)

# 4. Fix core/skills/init/SKILL.md
with open('core/skills/init/SKILL.md', 'r') as f:
    content = f.read()

content = content.replace(
    'prefills `{{ config_dir }}/team.toml`',
    'prefills `{{ agents_dir }}/team.toml`'
)
content = content.replace(
    'from `{{ config_dir }}/team.toml`',
    'from `{{ agents_dir }}/team.toml`'
)
content = content.replace(
    'Artifact 1 — `{{ config_dir }}/team.toml`',
    'Artifact 1 — `{{ agents_dir }}/team.toml`'
)
content = content.replace(
    'mkdir -p "$ROOT/{{ config_dir }}"\n# ... write the shown content to "$ROOT/{{ config_dir }}/team.toml"',
    'mkdir -p "$ROOT/{{ agents_dir }}"\n# ... write the shown content to "$ROOT/{{ agents_dir }}/team.toml"'
)

with open('core/skills/init/SKILL.md', 'w') as f:
    f.write(content)

# 5. Fix .gitignore
with open('.gitignore', 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.strip() == '.claude/team.toml':
        new_lines.append('.agents/team.toml\n')
        new_lines.append('.agents/team.config\n')
        new_lines.append(line)
    elif line.strip() == '.claude/settings.local.json':
        new_lines.append('.agents/settings.local.json\n')
        new_lines.append(line)
    else:
        new_lines.append(line)

with open('.gitignore', 'w') as f:
    f.writelines(new_lines)
