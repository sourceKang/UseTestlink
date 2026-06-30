import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core.cli import build_parser
from testlink_agent_core.errors import TestLinkError
from testlink_agent_core.testcases import (
    create_testcase_payload,
    flatten_plan_cases,
    normalize_testcase,
    update_testcase_payload,
)


class TestcaseTests(unittest.TestCase):
    def test_flattens_and_normalizes_plan_cases(self):
        raw = {
            "suite": {
                "case": {
                    "tcase_id": "100",
                    "full_external_id": "PRJ-100",
                    "tcase_name": "can_login",
                    "version": "2",
                    "execution_order": "1",
                }
            }
        }

        cases = flatten_plan_cases(raw)
        self.assertEqual(len(cases), 1)

        normalized = normalize_testcase(cases[0])
        self.assertEqual(normalized["external_id"], "PRJ-100")
        self.assertEqual(normalized["testcase_id"], "100")
        self.assertEqual(normalized["name"], "can_login")
        self.assertEqual(normalized["raw"], cases[0])

    def test_builds_create_testcase_payload(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "create-testcase",
                "--project",
                "EMS",
                "--suite-id",
                "55",
                "--name",
                "can_login",
                "--author-login",
                "alice",
                "--summary",
                "Verify login works.",
                "--step",
                "Open login page => Login page is shown",
                "--step",
                "Submit valid credentials => Dashboard is shown",
                "--importance",
                "high",
                "--execution-type",
                "automated",
            ]
        )

        payload = create_testcase_payload(args, {"id": "10", "name": "EMS"})

        self.assertEqual(payload["testprojectid"], "10")
        self.assertEqual(payload["testsuiteid"], "55")
        self.assertEqual(payload["testcasename"], "can_login")
        self.assertEqual(payload["authorlogin"], "alice")
        self.assertEqual(payload["summary"], "Verify login works.")
        self.assertEqual(payload["importance"], 3)
        self.assertEqual(payload["executiontype"], 2)
        self.assertEqual(payload["actiononduplicatedname"], "block")
        self.assertEqual(payload["steps"][0]["step_number"], 1)
        self.assertEqual(payload["steps"][0]["actions"], "Open login page")
        self.assertEqual(payload["steps"][0]["expected_results"], "Login page is shown")
        self.assertEqual(payload["steps"][0]["execution_type"], 2)

    def test_builds_create_testcase_payload_from_steps_file(self):
        parser = build_parser()
        with TemporaryDirectory() as tmpdir:
            steps_file = Path(tmpdir) / "steps.json"
            steps_file.write_text(
                """[
  {"actions": "Open settings", "expected_results": "Settings page is shown", "execution_type": "manual"},
  "Save changes => Success message is shown"
]""",
                encoding="utf-8",
            )
            args = parser.parse_args(
                [
                    "create-testcase",
                    "--project",
                    "EMS",
                    "--suite-id",
                    "55",
                    "--name",
                    "can_save_settings",
                    "--author-login",
                    "alice",
                    "--steps-file",
                    str(steps_file),
                    "--duplicate-action",
                    "generate-new",
                ]
            )

            payload = create_testcase_payload(args, {"id": "10", "name": "EMS"})

        self.assertEqual(payload["actiononduplicatedname"], "generate_new")
        self.assertEqual(len(payload["steps"]), 2)
        self.assertEqual(payload["steps"][0]["execution_type"], 1)
        self.assertEqual(payload["steps"][1]["expected_results"], "Success message is shown")

    def test_create_testcase_can_collapse_steps_into_one_testlink_row(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "create-testcase",
                "--project",
                "EMS",
                "--suite-id",
                "55",
                "--name",
                "single_row_case",
                "--author-login",
                "alice",
                "--step",
                "Login => Login succeeds",
                "--step",
                "Configure port => Port config is accepted",
                "--single-step",
                "--execution-type",
                "automated",
            ]
        )

        payload = create_testcase_payload(args, {"id": "10", "name": "EMS"})

        self.assertEqual(len(payload["steps"]), 1)
        self.assertEqual(payload["steps"][0]["step_number"], 1)
        self.assertEqual(payload["steps"][0]["actions"], "1. Login<br />\n2. Configure port")
        self.assertEqual(
            payload["steps"][0]["expected_results"],
            "1. Login succeeds<br />\n2. Port config is accepted",
        )
        self.assertEqual(payload["steps"][0]["execution_type"], 2)

    def test_create_testcase_accepts_suite_name_with_resolved_id(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "create-testcase",
                "--project",
                "Gateway",
                "--suite-name",
                "Root/API",
                "--name",
                "can_login",
                "--author-login",
                "alice",
                "--step",
                "Open login page => Login page is shown",
            ]
        )

        payload = create_testcase_payload(args, {"id": "20", "name": "Gateway"}, suite_id="99")

        self.assertEqual(payload["testprojectid"], "20")
        self.assertEqual(payload["testsuiteid"], "99")

    def test_create_testcase_preserves_multiline_rich_text_fields(self):
        parser = build_parser()
        with TemporaryDirectory() as tmpdir:
            preconditions_file = Path(tmpdir) / "preconditions.txt"
            steps_file = Path(tmpdir) / "steps.json"
            preconditions_file.write_text("Line 1\nLine 2", encoding="utf-8")
            steps_file.write_text(
                """[
  {"actions": "Action 1\\nAction 2", "expected_results": "Expected 1\\nExpected 2"}
]""",
                encoding="utf-8",
            )
            args = parser.parse_args(
                [
                    "create-testcase",
                    "--project",
                    "EMS",
                    "--suite-id",
                    "55",
                    "--name",
                    "can_preserve_lines",
                    "--author-login",
                    "alice",
                    "--summary",
                    "Summary 1\nSummary 2",
                    "--preconditions-file",
                    str(preconditions_file),
                    "--steps-file",
                    str(steps_file),
                ]
            )

            payload = create_testcase_payload(args, {"id": "10", "name": "EMS"})

        self.assertEqual(payload["summary"], "Summary 1<br />\nSummary 2")
        self.assertEqual(payload["preconditions"], "Line 1<br />\nLine 2")
        self.assertEqual(payload["steps"][0]["actions"], "Action 1<br />\nAction 2")
        self.assertEqual(payload["steps"][0]["expected_results"], "Expected 1<br />\nExpected 2")

    def test_builds_update_testcase_payload_with_only_requested_fields(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "update-testcase",
                "--testcase-external-id",
                "GW-123",
                "--summary",
                "Updated summary.",
                "--importance",
                "high",
            ]
        )

        payload = update_testcase_payload(args)

        self.assertEqual(payload["testcaseexternalid"], "GW-123")
        self.assertEqual(payload["summary"], "Updated summary.")
        self.assertEqual(payload["importance"], 3)
        self.assertNotIn("steps", payload)
        self.assertNotIn("executiontype", payload)

    def test_builds_update_testcase_payload_with_steps(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "update-testcase",
                "--testcase-id",
                "123",
                "--step",
                "Open VPN page => VPN page is shown",
            ]
        )

        payload = update_testcase_payload(args)

        self.assertEqual(payload["testcaseid"], "123")
        self.assertEqual(payload["steps"][0]["actions"], "Open VPN page")
        self.assertEqual(payload["steps"][0]["expected_results"], "VPN page is shown")
        self.assertEqual(payload["steps"][0]["execution_type"], 1)

    def test_update_testcase_can_collapse_steps_into_one_testlink_row(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "update-testcase",
                "--testcase-id",
                "123",
                "--step",
                "Action A => Expected A",
                "--step",
                "Action B => Expected B",
                "--single-step",
            ]
        )

        payload = update_testcase_payload(args)

        self.assertEqual(len(payload["steps"]), 1)
        self.assertEqual(payload["steps"][0]["actions"], "1. Action A<br />\n2. Action B")
        self.assertEqual(payload["steps"][0]["expected_results"], "1. Expected A<br />\n2. Expected B")

    def test_update_testcase_preserves_multiline_rich_text_fields(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "update-testcase",
                "--testcase-id",
                "123",
                "--preconditions",
                "Pre 1\nPre 2",
                "--step",
                "Action 1\nAction 2 => Expected 1\nExpected 2",
            ]
        )

        payload = update_testcase_payload(args)

        self.assertEqual(payload["preconditions"], "Pre 1<br />\nPre 2")
        self.assertEqual(payload["steps"][0]["actions"], "Action 1<br />\nAction 2")
        self.assertEqual(payload["steps"][0]["expected_results"], "Expected 1<br />\nExpected 2")

    def test_update_testcase_requires_a_field_to_update(self):
        parser = build_parser()
        args = parser.parse_args(["update-testcase", "--testcase-id", "123"])

        with self.assertRaises(TestLinkError):
            update_testcase_payload(args)


if __name__ == "__main__":
    unittest.main()
