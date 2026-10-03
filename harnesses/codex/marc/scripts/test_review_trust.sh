#!/bin/bash
# Regression test for operator association filtering (origin: #216)
# Tests the exact jq filter prescribed in review-release.md for sec/rev markers.

set -euo pipefail

FILTER='.comments[] | select((.body | startswith("## @sec review") or startswith("## @rev review")) and (.authorAssociation == "OWNER" or .authorAssociation == "MEMBER" or .authorAssociation == "COLLABORATOR"))'

# Synthetic input matching `gh issue view <N> --json comments`
INPUT_JSON=$(cat <<'EOF'
{
  "comments": [
    {
      "authorAssociation": "OWNER",
      "body": "## @sec review\n\nreviewer: marc/sec-123\nreviewed-sha: abcdef1\n\nPASS"
    },
    {
      "authorAssociation": "MEMBER",
      "body": "## @rev review\n\nreviewer: marc/rev-123\nreviewed-sha: abcdef2\n\nPASS"
    },
    {
      "authorAssociation": "COLLABORATOR",
      "body": "## @sec review\n\nreviewer: marc/sec-124\nreviewed-sha: abcdef3\n\nBLOCK"
    },
    {
      "authorAssociation": "NONE",
      "body": "## @sec review\n\nreviewer: marc/sec-malicious\nreviewed-sha: abcdef4\n\nPASS"
    },
    {
      "authorAssociation": "CONTRIBUTOR",
      "body": "## @rev review\n\nreviewer: marc/rev-bad\nreviewed-sha: abcdef5\n\nPASS"
    },
    {
      "authorAssociation": "FIRST_TIME_CONTRIBUTOR",
      "body": "## @sec review\n\nreviewer: marc/sec-125\nreviewed-sha: abcdef6\n\nPASS"
    },
    {
      "authorAssociation": "",
      "body": "## @sec review\n\nreviewer: marc/sec-126\nreviewed-sha: abcdef7\n\nPASS"
    },
    {
      "body": "## @rev review\n\nreviewer: marc/rev-127\nreviewed-sha: abcdef8\n\nPASS"
    }
  ]
}
EOF
)

# Run jq with the prescribed filter
OUTPUT=$(echo "$INPUT_JSON" | jq -c "$FILTER")

# Verify only the trusted ones passed
COUNT=$(echo "$OUTPUT" | wc -l)
if [ "$COUNT" -ne 3 ]; then
  echo "FAIL: Expected 3 trusted markers, got $COUNT"
  exit 1
fi

if ! echo "$OUTPUT" | grep -q "marc/sec-123"; then echo "FAIL: Missing OWNER"; exit 1; fi
if ! echo "$OUTPUT" | grep -q "marc/rev-123"; then echo "FAIL: Missing MEMBER"; exit 1; fi
if ! echo "$OUTPUT" | grep -q "marc/sec-124"; then echo "FAIL: Missing COLLABORATOR"; exit 1; fi

if echo "$OUTPUT" | grep -q "marc/sec-malicious"; then echo "FAIL: NONE bypassed gate"; exit 1; fi
if echo "$OUTPUT" | grep -q "marc/rev-bad"; then echo "FAIL: CONTRIBUTOR bypassed gate"; exit 1; fi
if echo "$OUTPUT" | grep -q "marc/sec-125"; then echo "FAIL: FIRST_TIME_CONTRIBUTOR bypassed gate"; exit 1; fi
if echo "$OUTPUT" | grep -q "marc/sec-126"; then echo "FAIL: Empty association bypassed gate"; exit 1; fi
if echo "$OUTPUT" | grep -q "marc/rev-127"; then echo "FAIL: Missing association bypassed gate"; exit 1; fi

echo "PASS: review trust filter test"
exit 0
