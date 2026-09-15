#!/usr/bin/env python3
"""Self-test for scripts/generate_benchmark_fixture.py (issue #293 review,
`@rev` finding: the `sweep` benchmark task rests on an unverified bet about
this repo's file-size shape; this fixture generator removes that bet by
constructing a guard-favorable shape deliberately).

Stdlib only (no pytest); run directly: python3 test_generate_benchmark_fixture.py

Covers:
  * determinism — two independent calls to build_fixture() with the same
    arguments produce byte-identical content and the same total;
  * shape — a clear majority of the default file set is comfortably over the
    350-line read-guard threshold;
  * verifiability — the recorded total actually equals the sum of every
    signal value the content encodes (an independent word-to-number decode of
    the generated sentences, not a re-use of the generator's own running
    total, so this would catch a bug in the total's bookkeeping too);
  * decoys — the module/pass id digits embedded in each sentence are NOT
    signal values (a naive digit-sum over the raw text would NOT equal the
    recorded total), which is the property that makes a full read the natural
    strategy rather than a `grep -oE '[0-9]+'` one-liner;
  * num_to_words — spot checks across the 1..59 supported range, including
    the sole irregular boundary (10-19) and the round tens.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import generate_benchmark_fixture as gbf  # noqa: E402

_failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        _failures.append(message)


_WORD_TO_NUM = {}
for _n in range(1, 60):
    _WORD_TO_NUM[gbf.num_to_words(_n)] = _n


def decode_signal_words(content: str) -> int:
    """Independently re-derive the total from the generated text: every
    line's signal word is either a single word (one..nineteen) or a
    hyphenated tens-word (twenty-one, fifty-nine, ...). This walks each line
    looking for the LONGEST word/hyphenated-word run that matches a known
    number word, distinct from the bare digit tokens (module/pass ids) also
    present in the line.
    """
    total = 0
    token_re = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)?")
    for line in content.splitlines():
        if not line.strip():
            continue
        found = None
        for tok in token_re.findall(line):
            low = tok.lower()
            if low in _WORD_TO_NUM:
                found = low
                break
        check(found is not None, f"no recognizable number-word found in line: {line!r}")
        if found is not None:
            total += _WORD_TO_NUM[found]
    return total


def test_determinism():
    files_a, total_a, per_file_a = gbf.build_fixture()
    files_b, total_b, per_file_b = gbf.build_fixture()
    check(files_a == files_b, "build_fixture() is not deterministic: two calls produced different content")
    check(total_a == total_b, "build_fixture() is not deterministic: two calls produced different totals")
    check(per_file_a == per_file_b, "build_fixture() is not deterministic: two calls produced different per-file totals")


def test_majority_of_files_comfortably_over_guard_threshold():
    threshold = 350
    comfortable_margin = 50  # "comfortably" above, not just barely over
    sizes = gbf.DEFAULT_FILE_SIZES
    over = [s for s in sizes if s >= threshold + comfortable_margin]
    check(
        len(over) > len(sizes) / 2,
        f"expected a clear majority of {len(sizes)} files comfortably (>={threshold + comfortable_margin}) over the "
        f"{threshold}-line guard threshold, got only {len(over)}: {sizes}",
    )
    under = [s for s in sizes if s < threshold]
    check(len(under) >= 1, "expected at least one file under the guard threshold for shape variety, found none")


def test_recorded_total_matches_independent_word_decode():
    files, total, per_file_totals = gbf.build_fixture()
    check(sum(per_file_totals) == total, "per-file totals do not sum to the recorded grand total")
    decoded_total = 0
    for name in sorted(files):
        decoded_total += decode_signal_words(files[name])
    check(
        decoded_total == total,
        f"independent word-decode of the generated content ({decoded_total}) does not match the recorded "
        f"ground-truth total ({total}) -- a wrong answer would not be detectable",
    )


def test_decoy_digits_are_not_the_signal():
    """A lazy digit-sum over the raw text (ignoring the spelled-out words
    entirely) must NOT equal the ground-truth total -- this is the property
    that makes reading (not grep/digit-sum) the natural strategy."""
    files, total, _ = gbf.build_fixture()
    digit_sum = 0
    for content in files.values():
        digit_sum += sum(int(d) for d in re.findall(r"\d+", content))
    check(
        digit_sum != total,
        "a naive digit-sum over the fixture text equals the ground-truth total -- decoy digits are not "
        "effectively distinguishing the signal from noise",
    )


def test_num_to_words_spot_checks():
    check(gbf.num_to_words(1) == "one", "num_to_words(1) wrong")
    check(gbf.num_to_words(9) == "nine", "num_to_words(9) wrong")
    check(gbf.num_to_words(10) == "ten", "num_to_words(10) wrong")
    check(gbf.num_to_words(17) == "seventeen", "num_to_words(17) wrong")
    check(gbf.num_to_words(19) == "nineteen", "num_to_words(19) wrong")
    check(gbf.num_to_words(20) == "twenty", "num_to_words(20) wrong")
    check(gbf.num_to_words(21) == "twenty-one", "num_to_words(21) wrong")
    check(gbf.num_to_words(59) == "fifty-nine", "num_to_words(59) wrong")
    try:
        gbf.num_to_words(60)
        check(False, "num_to_words(60) should have raised ValueError (out of supported range)")
    except ValueError:
        pass
    try:
        gbf.num_to_words(0)
        check(False, "num_to_words(0) should have raised ValueError (out of supported range)")
    except ValueError:
        pass


def test_write_fixture_expected_file_outside_out_dir(tmp_path_str):
    out_dir = os.path.join(tmp_path_str, "fixture-out")
    expected_file = os.path.join(tmp_path_str, "fixture-out.expected.json")
    total = gbf.write_fixture(out_dir, expected_file=expected_file)
    check(os.path.isdir(out_dir), "write_fixture did not create out_dir")
    check(
        not os.path.exists(os.path.join(out_dir, os.path.basename(expected_file))),
        "the expected-answer file must not be written inside out_dir (would contaminate the agent-visible fixture)",
    )
    check(os.path.isfile(expected_file), "write_fixture did not write the expected-answer file")
    written_files = sorted(f for f in os.listdir(out_dir) if f.startswith("fixture_"))
    check(
        written_files == sorted(gbf.build_fixture()[0].keys()),
        f"unexpected file set written to out_dir: {written_files}",
    )
    check(isinstance(total, int) and total > 0, f"write_fixture returned a non-positive total: {total}")


def main() -> int:
    import tempfile

    test_determinism()
    test_majority_of_files_comfortably_over_guard_threshold()
    test_recorded_total_matches_independent_word_decode()
    test_decoy_digits_are_not_the_signal()
    test_num_to_words_spot_checks()
    with tempfile.TemporaryDirectory() as tmp:
        test_write_fixture_expected_file_outside_out_dir(tmp)

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for m in _failures:
            print(f"  - {m}")
        return 1
    print("\nAll generate_benchmark_fixture self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
