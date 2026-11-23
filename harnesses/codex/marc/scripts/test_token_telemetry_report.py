#!/usr/bin/env python3
"""Self-test for the token_telemetry_report script."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import token_telemetry_report as tr  # noqa: E402


class TestTokenTelemetryReport(unittest.TestCase):
    def setUp(self):
        self.maxDiff = None
        self.tmpdir = tempfile.TemporaryDirectory()
        self.baseline_path = os.path.join(self.tmpdir.name, "baseline.jsonl")
        self.post_path = os.path.join(self.tmpdir.name, "post.jsonl")
        
        # Write some baseline data (sum of weighted: 1000 + 500 = 1500)
        with open(self.baseline_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": "sess-1", "weighted": 1000, "turns": 1, "model": "test-model"}) + "\n")
            f.write(json.dumps({"session_id": "sess-2", "weighted": 500, "turns": 2, "model": "test-model"}) + "\n")
            
        # Write some post data (sum of weighted: 500 + 400 = 900)
        with open(self.post_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": "sess-3", "weighted": 500, "turns": 1, "model": "test-model"}) + "\n")
            f.write(json.dumps({"session_id": "sess-4", "weighted": 400, "turns": 1, "model": "test-model"}) + "\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def run_main(self, args: list[str]) -> tuple[int, str]:
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = tr.main(args)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
        return rc, output

    def test_report_basic(self):
        rc, out = self.run_main(["--path", self.baseline_path])
        self.assertEqual(rc, 0)
        self.assertIn("sess-1", out)
        self.assertIn("sess-2", out)
        self.assertIn("weighted tokens (shown sessions): 1500", out)

    def test_cache_write_bucket(self):
        path = os.path.join(self.tmpdir.name, "cw.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": "sess-cw", "weighted": 1000, "cache_write": 300,
                                "cache_write_weighted": 250, "sc_cache_write": 100,
                                "sc_cache_write_weighted": 125, "schema_version": 2}) + "\n")
        rc, out = self.run_main(["--path", path])
        self.assertEqual(rc, 0)
        row = [l for l in out.splitlines() if l.startswith("sess-cw")][0].split()
        self.assertEqual(row[row.index("300"):row.index("300") + 4], ["300", "250", "25%", "125"])

    def test_legacy_records_show_dash_and_mixed_warns(self):
        rc, out = self.run_main(["--path", self.baseline_path])
        row = [l for l in out.splitlines() if l.startswith("sess-1")][0].split()
        self.assertEqual(row[-4:-1], ["0", "-", "0"])
        new = os.path.join(self.tmpdir.name, "v2.jsonl")
        with open(new, "w", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": "s9", "weighted": 100, "schema_version": 2}) + "\n")
        rc, out = self.run_main(["--path", self.baseline_path, "--compare", new])
        self.assertIn("different `weighted` semantics", out)
        rc, out = self.run_main(["--path", self.post_path, "--compare", self.baseline_path])
        self.assertNotIn("different `weighted` semantics", out)

    def test_reemitted_turn_counts_once(self):
        path = os.path.join(self.tmpdir.name, "re.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for w in (100, 700):
                f.write(json.dumps({"session_id": "s", "turn_index": 0, "weighted": w,
                                    "schema_version": 2}) + "\n")
        rc, out = self.run_main(["--path", path])
        self.assertIn("weighted tokens (shown sessions): 700   turns: 1", out)

    def test_report_empty(self):
        empty_path = os.path.join(self.tmpdir.name, "empty.jsonl")
        open(empty_path, "w").close()
        rc, out = self.run_main(["--path", empty_path])
        self.assertEqual(rc, 0)
        self.assertIn("exists but has no records yet", out)

    def test_report_missing(self):
        missing_path = os.path.join(self.tmpdir.name, "does-not-exist.jsonl")
        rc, out = self.run_main(["--path", missing_path])
        self.assertEqual(rc, 0)
        self.assertIn("telemetry is opt-in and OFF by default", out)

    def test_comparison_mode(self):
        rc, out = self.run_main(["--path", self.baseline_path, "--compare", self.post_path])
        self.assertEqual(rc, 0)
        self.assertIn("--- Token Savings Comparison ---", out)
        self.assertIn("Baseline tokens (weighted): 1,500", out)
        self.assertIn("Post-opt tokens (weighted): 900", out)
        self.assertIn("Difference:                 600 tokens saved", out)
        self.assertIn("Percentage saved:           40.0%", out)
        self.assertIn("Cost delta:                 $0.0018 saved", out)

    def test_comparison_mode_custom_cost(self):
        rc, out = self.run_main(["--path", self.baseline_path, "--compare", self.post_path, "--cost-per-million", "10.0"])
        self.assertEqual(rc, 0)
        self.assertIn("Cost delta:                 $0.0060 saved", out)


if __name__ == "__main__":
    unittest.main()
