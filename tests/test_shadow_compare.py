from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from qa_integration_agent.shadow import compare_preview_files, compare_previews


def legacy_preview():
    return {
        "target": {
            "project": "EMS",
            "testplan": "Regression",
            "platform": "Default Platform",
            "build": "build-1",
        },
        "write_count": 1,
        "ignored_count": 0,
        "failures_to_write": [{"external_id": "EMS-1"}],
        "redmine": {
            "issues_to_create_or_reuse": [
                {"external_id": "EMS-1", "action": "create-or-reuse"}
            ]
        },
    }


def modern_preview():
    return {
        "target": {
            "project": "EMS",
            "plan": "Regression",
            "platform": "Default Platform",
            "build": "build-1",
        },
        "write_count": 1,
        "ignored_count": 0,
        "items": [
            {
                "testcase_external_id": "EMS-1",
                "status": "f",
                "redmine_action": "create",
            }
        ],
    }


class ShadowCompareTests(unittest.TestCase):
    def test_compatible_legacy_create_or_reuse_matches_modern_decision(self) -> None:
        result = compare_previews(legacy_preview(), modern_preview())

        self.assertTrue(result["compatible"])
        self.assertEqual(0, result["mismatch_count"])

    def test_target_and_failure_differences_are_reported(self) -> None:
        modern = modern_preview()
        modern["target"]["platform"] = "Ubuntu"
        modern["items"][0]["testcase_external_id"] = "EMS-2"

        result = compare_previews(legacy_preview(), modern)

        self.assertFalse(result["compatible"])
        fields = {item["field"] for item in result["mismatches"]}
        self.assertIn("target.platform", fields)
        self.assertIn("failure_testcases", fields)

    def test_file_comparison_accepts_mcp_result_envelopes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            legacy_path = Path(tmpdir) / "legacy.json"
            modern_path = Path(tmpdir) / "modern.json"
            legacy_path.write_text(json.dumps({"ok": True, "result": legacy_preview()}), encoding="utf-8")
            modern_path.write_text(json.dumps({"ok": True, "result": modern_preview()}), encoding="utf-8")

            result = compare_preview_files(str(legacy_path), str(modern_path))

        self.assertTrue(result["compatible"])


if __name__ == "__main__":
    unittest.main()
