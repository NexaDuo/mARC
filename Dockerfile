FROM node:22-bookworm

# Install Python, pip, and system utilities
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    shellcheck \
    jq \
    git \
    curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI (gh)
RUN type -p curl >/dev/null || (apt-get update && apt-get install curl -y) \
    && mkdir -p -m 755 /etc/apt/keyrings \
    && out=$(mktemp) && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o "$out" \
    && gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg "$out" \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install gh -y

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code@2.1.200

# Install Google Antigravity Python SDK & CLI
RUN pip3 install --break-system-packages google-antigravity==0.1.6

# Set the workspace directory
WORKDIR /workspace
