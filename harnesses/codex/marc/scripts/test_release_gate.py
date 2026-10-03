#!/usr/bin/env python3
"""Self-test for `release_gate.py`'s `is_released()` decision (origin: #75).

Stdlib only (no pytest); run directly:  python3 test_release_gate.py

Deterministic, offline, zero token cost — no real repo, no network, no `gh`/
`git` calls. Feeds `is_released()` synthetic tag/release lists and asserts the
PASS/FAIL verdict, matching the pattern used by `test_board_reconcile.py` and
`test_script_parity.py`.

Covers the three acceptance-criteria cases from #75:
  * released-ok:                tag present + published (non-draft) release -> PASS
  * missing-tag:                no matching tag at all                      -> FAIL
  * tag-exists-but-release-draft/absent:
      - tag present, no release at all                                     -> FAIL
      - tag present, release present but still a draft                     -> FAIL
Plus a version-mismatch guard (a tag/release for a DIFFERENT version must not
false-positive) and the reason text sanity checks (actionable output).
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from release_gate import ReleaseInfo, is_released  # noqa: E402

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


def main() -> int:
    # --- released-ok: tag + published release both present -> PASS ---------
    result = is_released(
        "0.16.6",
        tags=["v0.16.4", "v0.16.5", "v0.16.6"],
        releases=[
            ReleaseInfo(tag_name="v0.16.4", is_draft=False),
            ReleaseInfo(tag_name="v0.16.5", is_draft=False),
            ReleaseInfo(tag_name="v0.16.6", is_draft=False),
        ],
    )
    check(result.ok is True, "released-ok fixture: is_released() == True")
    check(result.tag_found and result.release_found, "released-ok fixture: both tag_found and release_found True")

    # --- missing-tag: version bumped, no tag pushed at all -> FAIL ----------
    result = is_released(
        "0.16.6",
        tags=["v0.16.4", "v0.16.5"],
        releases=[
            ReleaseInfo(tag_name="v0.16.4", is_draft=False),
            ReleaseInfo(tag_name="v0.16.5", is_draft=False),
        ],
    )
    check(result.ok is False, "missing-tag fixture: is_released() == False")
    check(result.tag_found is False, "missing-tag fixture: tag_found False")
    check("no git tag" in result.reason, f"missing-tag fixture: reason mentions missing tag ({result.reason!r})")

    # --- tag exists, no release at all -> FAIL -------------------------------
    result = is_released(
        "0.16.6",
        tags=["v0.16.4", "v0.16.5", "v0.16.6"],
        releases=[
            ReleaseInfo(tag_name="v0.16.4", is_draft=False),
            ReleaseInfo(tag_name="v0.16.5", is_draft=False),
        ],
    )
    check(result.ok is False, "tag-exists-no-release fixture: is_released() == False")
    check(result.tag_found is True, "tag-exists-no-release fixture: tag_found True")
    check(result.release_found is False, "tag-exists-no-release fixture: release_found False")
    check("no GitHub Release" in result.reason,
          f"tag-exists-no-release fixture: reason mentions missing release ({result.reason!r})")

    # --- tag exists, release exists but is a DRAFT -> FAIL -------------------
    result = is_released(
        "0.16.6",
        tags=["v0.16.4", "v0.16.5", "v0.16.6"],
        releases=[
            ReleaseInfo(tag_name="v0.16.4", is_draft=False),
            ReleaseInfo(tag_name="v0.16.5", is_draft=False),
            ReleaseInfo(tag_name="v0.16.6", is_draft=True),
        ],
    )
    check(result.ok is False, "draft-release fixture: is_released() == False")
    check(result.tag_found is True, "draft-release fixture: tag_found True")
    check(result.release_found is False, "draft-release fixture: release_found False (draft doesn't count)")
    check("DRAFT" in result.reason, f"draft-release fixture: reason mentions draft ({result.reason!r})")

    # --- version-mismatch guard: unrelated tags/releases must not false-positive
    result = is_released(
        "0.17.0",
        tags=["v0.16.4", "v0.16.5", "v0.16.6"],
        releases=[
            ReleaseInfo(tag_name="v0.16.6", is_draft=False),
        ],
    )
    check(result.ok is False, "version-mismatch fixture: is_released() == False")
    check(result.expected_tag == "v0.17.0", "version-mismatch fixture: expected_tag derived from the given version")

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1

    print("\nrelease_gate self-test: OK (all fixtures behaved as expected).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
