#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHANGELOG_SCRIPT="${HERE}/changelog-section.sh"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
cd "$tmpdir"

cat > CHANGELOG.md <<EOF
# Changelog

## [1.2.3] - 2024-01-01
This is 1.2.3.

## [1.2.4] - 2024-01-02
This is 1.2.4.

## [1.2.4|1.2.3] - 2024-01-03
Malicious tag match.

## [1.2X3] - 2024-01-04
Wildcard tag match.
EOF

# Normal match
"$CHANGELOG_SCRIPT" "1.2.3" CHANGELOG.md > out.1.2.3
if ! grep -q "This is 1.2.3" out.1.2.3; then
    echo "FAIL: Expected to find 1.2.3 content"
    exit 1
fi

# The malicious regex tag "1.2.4|1.2.3" should only match its exact section.
"$CHANGELOG_SCRIPT" "1.2.4|1.2.3" CHANGELOG.md > out.malicious
if ! grep -q "Malicious tag match" out.malicious; then
    echo "FAIL: Expected to find Malicious tag match content"
    exit 1
fi
if grep -q "This is 1.2.3" out.malicious; then
    echo "FAIL: Regex injection '1.2.4|1.2.3' successfully matched 1.2.3 section!"
    exit 1
fi

# Test that dots are not treated as wildcards
if "$CHANGELOG_SCRIPT" "1.2.3" CHANGELOG.md | grep -q "Wildcard tag match"; then
    echo "FAIL: dot in '1.2.3' acted as regex wildcard and matched '1.2X3'"
    exit 1
fi

echo "PASS: changelog-section tests"
