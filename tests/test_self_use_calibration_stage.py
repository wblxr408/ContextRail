"""I08 reports observable measures and leaves absent native evidence unknown."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from examples import native_client_acceptance


class SelfUseCalibrationTests(unittest.TestCase):
    def test_reports_paired_bytes_exact_cold_read_and_explicit_unknowns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, expected = native_client_acceptance.fixture(root / "校准任务")
            case = root / "case.json"
            case.write_text(json.dumps({
                "schema": 1, "workspace": data["workspace"], "task": data["scope"]["task"],
                "session": "owner", "snapshot": data["snapshot"], "budget": 7000,
                "oracle": {"required_ids": ["R001"], "next_action": None,
                           "cold_ref": {**data["cold_ref"], "start": data["start"]},
                           "cold_text": expected["cold_tail"]},
            }), encoding="utf-8")
            result = subprocess.run([sys.executable, "-m", "contextrail.evaluation", "self-use", "--case", str(case)],
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            report = json.loads(result.stdout)
            raw = report["comparison"]["full_source"]
            recovered = report["comparison"]["packet_with_cold_read"]
            self.assertEqual(recovered["missing_required_ids"], [])
            self.assertTrue(recovered["cold_text_exact"])
            self.assertEqual(recovered["cold_body_bytes"], len(expected["cold_tail"].encode()))
            self.assertGreater(raw["delivered_bytes"], recovered["delivered_bytes"])
            self.assertIsNone(recovered["next_action_correct"])
            self.assertIsNone(report["scenarios"]["client_interruption"])
            self.assertIsNone(report["scenarios"]["model_unavailable"])
            self.assertIsNone(report["provider_tokens"])
