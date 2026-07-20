#!/usr/bin/env python3
"""Self-test for `release_verify.py`'s `verify_release()` decision (origin: #113).

Stdlib only (no pytest); run directly:  python3 test_release_verify.py

Deterministic, offline, zero token cost — no real repo, no network, no `gh`/
`git` calls. Feeds `verify_release()` synthetic tag/workflow-run/release
fixtures and asserts the PASS/FAIL verdict per check, matching the pattern
used by `test_release_gate.py` and `test_board_reconcile.py`.

Covers the three acceptance-criteria checks from #113:
  * all-green:            tag exists + workflow run succeeded + release
                           published and marked Latest -> ok
  * missing-tag:           tag doesn't exist                     -> FAIL (tag_exists)
  * no-workflow-run:       no run found for the tag               -> FAIL (workflow_run)
  * workflow-still-running: run status not yet 'completed'        -> FAIL (workflow_run)
  * workflow-failed:       run completed with conclusion != success -> FAIL (workflow_run)
  * no-release:            tag exists, no matching Release         -> FAIL (release_published)
  * draft-release:         Release exists but still a draft        -> FAIL (release_published)
  * not-latest:            Release published, not marked Latest    -> FAIL (release_published)
  * leading-v normalization: "v0.16.7" and "0.16.7" behave identically
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from release_verify import (  # noqa: E402
    ReleaseRow,
    WorkflowRun,
    verify_release,
)

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


GREEN_RUN = [WorkflowRun(status="completed", conclusion="success", url="https://x/runs/1")]
GREEN_RELEASE = [ReleaseRow(tag_name="v0.16.7", is_draft=False, is_latest=True)]


def main() -> int:
    # --- all-green: every check passes -> ok ---------------------------------
    result = verify_release("0.16.7", tag_exists=True, runs=GREEN_RUN, releases=GREEN_RELEASE)
    check(result.ok is True, "all-green fixture: verify_release().ok == True")
    check(result.tag_exists.ok and result.workflow_run.ok and result.release_published.ok,
          "all-green fixture: all three checks individually ok")
    check(result.expected_tag == "v0.16.7", "all-green fixture: expected_tag derived from version")

    # --- missing-tag ----------------------------------------------------------
    result = verify_release("0.16.7", tag_exists=False, runs=GREEN_RUN, releases=GREEN_RELEASE)
    check(result.ok is False, "missing-tag fixture: overall ok == False")
    check(result.tag_exists.ok is False, "missing-tag fixture: tag_exists.ok == False")
    check(result.workflow_run.ok is True, "missing-tag fixture: workflow_run unaffected")

    # --- no-workflow-run --------------------------------------------------------
    result = verify_release("0.16.7", tag_exists=True, runs=[], releases=GREEN_RELEASE)
    check(result.ok is False, "no-workflow-run fixture: overall ok == False")
    check(result.workflow_run.ok is False, "no-workflow-run fixture: workflow_run.ok == False")
    check("no release-workflow run" in result.workflow_run.notes,
          f"no-workflow-run fixture: notes mention no run found ({result.workflow_run.notes!r})")

    # --- workflow-still-running --------------------------------------------------
    running = [WorkflowRun(status="in_progress", conclusion=None, url="https://x/runs/2")]
    result = verify_release("0.16.7", tag_exists=True, runs=running, releases=GREEN_RELEASE)
    check(result.ok is False, "workflow-still-running fixture: overall ok == False")
    check(result.workflow_run.ok is False, "workflow-still-running fixture: workflow_run.ok == False")
    check("in_progress" in result.workflow_run.notes,
          f"workflow-still-running fixture: notes mention status ({result.workflow_run.notes!r})")

    # --- workflow-failed ------------------------------------------------------
    failed = [WorkflowRun(status="completed", conclusion="failure", url="https://x/runs/3")]
    result = verify_release("0.16.7", tag_exists=True, runs=failed, releases=GREEN_RELEASE)
    check(result.ok is False, "workflow-failed fixture: overall ok == False")
    check(result.workflow_run.ok is False, "workflow-failed fixture: workflow_run.ok == False")
    check("failure" in result.workflow_run.notes,
          f"workflow-failed fixture: notes mention conclusion ({result.workflow_run.notes!r})")

    # --- no-release -------------------------------------------------------------
    result = verify_release("0.16.7", tag_exists=True, runs=GREEN_RUN, releases=[])
    check(result.ok is False, "no-release fixture: overall ok == False")
    check(result.release_published.ok is False, "no-release fixture: release_published.ok == False")
    check("no GitHub Release" in result.release_published.notes,
          f"no-release fixture: notes mention missing release ({result.release_published.notes!r})")

    # --- draft-release ----------------------------------------------------------
    draft = [ReleaseRow(tag_name="v0.16.7", is_draft=True, is_latest=False)]
    result = verify_release("0.16.7", tag_exists=True, runs=GREEN_RUN, releases=draft)
    check(result.ok is False, "draft-release fixture: overall ok == False")
    check(result.release_published.ok is False, "draft-release fixture: release_published.ok == False")
    check("DRAFT" in result.release_published.notes,
          f"draft-release fixture: notes mention draft ({result.release_published.notes!r})")

    # --- not-latest --------------------------------------------------------------
    not_latest = [ReleaseRow(tag_name="v0.16.7", is_draft=False, is_latest=False)]
    result = verify_release("0.16.7", tag_exists=True, runs=GREEN_RUN, releases=not_latest)
    check(result.ok is False, "not-latest fixture: overall ok == False")
    check(result.release_published.ok is False, "not-latest fixture: release_published.ok == False")
    check("NOT marked 'Latest'" in result.release_published.notes,
          f"not-latest fixture: notes mention Latest ({result.release_published.notes!r})")

    # --- leading-v normalization: "v0.16.7" behaves identically to "0.16.7" ------
    result_v = verify_release("v0.16.7", tag_exists=True, runs=GREEN_RUN, releases=GREEN_RELEASE)
    result_plain = verify_release("0.16.7", tag_exists=True, runs=GREEN_RUN, releases=GREEN_RELEASE)
    check(result_v.ok == result_plain.ok == True, "leading-v fixture: both forms verify ok")
    check(result_v.expected_tag == result_plain.expected_tag == "v0.16.7",
          "leading-v fixture: both forms derive the same expected_tag")
    check(result_v.version == result_plain.version == "0.16.7",
          "leading-v fixture: both forms normalize to the same version string")

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1

    print("\nrelease_verify self-test: OK (all fixtures behaved as expected).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
