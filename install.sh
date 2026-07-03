#!/usr/bin/env bash
#
# mARC (Multi-Agent Relay Control) — installer
# -----------------------------------------------------------------------------
# What this does, in order, and NOTHING else:
#   1. Prints the mARC banner.
#   2. Verifies the `claude` CLI is on your PATH.
#   3. Adds the mARC marketplace (GitHub repo NexaDuo/mARC, or a local path).
#   4. Installs the `marc` plugin from that marketplace (user scope).
#
# SAFETY / AUDITABILITY (read before running):
#   - No `curl | sh`. No remote code is fetched and executed by this script.
#   - The only network action is Claude Code's own, well-known plugin mechanism
#     (`claude plugin marketplace add` / `claude plugin install`), which clones
#     the marketplace repo you can inspect at https://github.com/NexaDuo/mARC.
#   - Every step is echoed before it runs (`run()` prints the command).
#   - No sudo, no writes outside Claude Code's own plugin store.
#   - Override the source with:  MARC_SOURCE=./  ./install.sh   (local checkout).
# -----------------------------------------------------------------------------
set -euo pipefail

MARC_SOURCE="${MARC_SOURCE:-NexaDuo/mARC}"   # GitHub repo slug, URL, or local path
MARKETPLACE_NAME="nexaduo"
PLUGIN_ID="marc@nexaduo"

banner() {
  cat <<'BANNER'
        ███╗   ███╗  █████╗  ██████╗   ██████╗
        ████╗ ████║ ██╔══██╗ ██╔══██╗ ██╔════╝
        ██╔████╔██║ ███████║ ██████╔╝ ██║
        ██║╚██╔╝██║ ██╔══██║ ██╔══██╗ ██║
        ██║ ╚═╝ ██║ ██║  ██║ ██║  ██║ ╚██████╗
        ╚═╝     ╚═╝ ╚═╝  ╚═╝ ╚═╝  ╚═╝  ╚═════╝
   ▄▖▄▖▄▖▄▖ multi · agent · relay · control ▄▖▄▖▄▖▄▖
   *** joining #marc — @techlead will op the channel ***
BANNER
}

# Echo a command, then run it. Nothing runs that you don't see first.
run() {
  printf '  >> %s\n' "$*"
  "$@"
}

main() {
  banner

  if ! command -v claude >/dev/null 2>&1; then
    echo "ERROR: the 'claude' CLI is not on your PATH." >&2
    echo "Install Claude Code first: https://claude.com/claude-code" >&2
    exit 1
  fi

  echo
  echo "[1/2] Adding the mARC marketplace (source: ${MARC_SOURCE})"
  run claude plugin marketplace add "${MARC_SOURCE}"

  echo
  echo "[2/2] Installing the '${PLUGIN_ID}' plugin (user scope)"
  run claude plugin install "${PLUGIN_ID}"

  echo
  echo "*** mARC installed. Open any repo and run /marc:tech-lead to op the channel. ***"
  echo "*** Update later with:  claude plugin update ${PLUGIN_ID}"
}

main "$@"
