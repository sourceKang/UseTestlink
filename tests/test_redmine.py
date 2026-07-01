import unittest
import json
import os
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core.errors import RedmineError
from testlink_agent_core.models import ParsedResult, RedmineIssue
from testlink_agent_core.redmine import (
    build_existing_redmine_issue,
    build_notes,
    build_redmine_issue_payload,
)


class RedmineTests(unittest.TestCase):
    def setUp(self):
        self.saved_env = {
            key: os.environ.get(key)
            for key in (
                "REDMINE_ALLOW_MANAGER_FIELDS",
                "REDMINE_ASSIGNED_TO_ID",
                "REDMINE_FIXED_VERSION_ID",
                "REDMINE_TEMPLATE",
                "REDMINE_PROJECT_ID",
                "REDMINE_TRACKER_ID",
                "REDMINE_STATUS_ID",
                "REDMINE_PRIORITY_ID",
                "REDMINE_CATEGORY_ID",
            )
        }
        for key in self.saved_env:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_builds_redmine_issue_payload(self):
        args = Namespace(
            redmine_project="ems",
            redmine_tracker_id="1",
            redmine_status_id="",
            redmine_priority_id="2",
            redmine_assigned_to_id="",
            redmine_category_id="",
            redmine_fixed_version_id="",
        )
        header = {
            "Report generated on": "2026-06-12_13-26-09",
            "EMS Version": "1.2.3 build 5",
            "Node Name": "Example_Node",
            "Node IP": "192.0.2.10",
        }
        result = ParsedResult(
            external_id="PRJ-6682",
            test_name="test_get_port_by_devicename",
            raw_status="Fail",
            status="f",
            duration_text="0s",
            duration_seconds=0.0,
            testlink_name="/port/$devicename - GET",
        )
        context = {
            "project": {"name": "EMS"},
            "plan": {"name": "Regression"},
            "platform": {"name": "NetAtlas EMS"},
            "build": {"name": "03.00.11(AAVV.221)b5"},
        }

        payload = build_redmine_issue_payload(args, header, Path("report.txt"), result, context)

        self.assertEqual(payload["project_id"], "ems")
        self.assertEqual(payload["tracker_id"], 1)
        self.assertEqual(payload["priority_id"], 2)
        self.assertNotIn("assigned_to_id", payload)
        self.assertNotIn("fixed_version_id", payload)
        self.assertIn("[PRJ-6682]", payload["subject"])
        self.assertIn("Test case: PRJ-6682", payload["description"])
        self.assertIn("Platform: NetAtlas EMS", payload["description"])

    def test_builds_custom_fields_from_redmine_template(self):
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "netatlas-ems_pqa",
                        "tracker_id": 1,
                        "priority_id": 5,
                        "required_custom_fields": ["Severity", "FW Ver", "Test case No", "Report Date"],
                        "custom_fields": [
                            {"id": 10, "name": "Severity", "value": "Major"},
                            {"id": 11, "name": "FW Ver", "value": "{{header.EMS Version}}"},
                            {"id": 12, "name": "Test case No", "value": "{{result.external_id}}"},
                            {"id": 13, "name": "Report Date", "value": "{{report_date}}"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                redmine_project="",
                redmine_template=str(template_path),
                redmine_custom_fields=None,
                redmine_tracker_id="",
                redmine_status_id="",
                redmine_priority_id="",
                redmine_assigned_to_id="",
                redmine_category_id="",
                redmine_fixed_version_id="",
            )
            header = {
                "Report generated on": "2026-06-12_13-26-09",
                "EMS Version": "1.2.3 build 5",
            }
            result = ParsedResult(
                external_id="EMS1-7128",
                test_name="test_login",
                raw_status="Fail",
                status="f",
                duration_text="1s",
                duration_seconds=1.0,
            )
            context = {
                "project": {"name": "EMS"},
                "plan": {"name": "Regression"},
                "platform": {"name": "NetAtlas EMS"},
                "build": {"name": "03.00.11(AAVV.221)b5"},
            }

            payload = build_redmine_issue_payload(args, header, Path("report.txt"), result, context)

        self.assertEqual(payload["project_id"], "netatlas-ems_pqa")
        self.assertEqual(payload["tracker_id"], 1)
        self.assertEqual(payload["priority_id"], 5)
        self.assertEqual(
            payload["custom_fields"],
            [
                {"id": 10, "value": "Major"},
                {"id": 11, "value": "1.2.3 build 5"},
                {"id": 12, "value": "EMS1-7128"},
                {"id": 13, "value": "2026-06-12"},
            ],
        )

    def test_redmine_template_can_clear_fixed_version(self):
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "netatlas-ems_pqa",
                        "tracker_id": 1,
                        "priority_id": 5,
                        "fixed_version_id": "",
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                redmine_project="",
                redmine_template=str(template_path),
                redmine_custom_fields=None,
                redmine_tracker_id="",
                redmine_status_id="",
                redmine_priority_id="",
                redmine_assigned_to_id="",
                redmine_category_id="",
                redmine_fixed_version_id="",
            )
            result = ParsedResult(
                external_id="EMS1-7128",
                test_name="test_login",
                raw_status="Fail",
                status="f",
                duration_text="1s",
                duration_seconds=1.0,
            )
            context = {
                "project": {"name": "EMS"},
                "plan": {"name": "Regression"},
                "platform": {"name": "NetAtlas EMS"},
                "build": {"name": "03.00.11(AAVV.221)b5"},
            }

            payload = build_redmine_issue_payload(args, {}, Path("report.txt"), result, context)

        self.assertEqual(payload["fixed_version_id"], "")

    def test_redmine_template_nonempty_fixed_version_requires_manager_switch(self):
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "netatlas-ems_pqa",
                        "tracker_id": 1,
                        "priority_id": 5,
                        "fixed_version_id": "9",
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                redmine_project="",
                redmine_template=str(template_path),
                redmine_custom_fields=None,
                redmine_tracker_id="",
                redmine_status_id="",
                redmine_priority_id="",
                redmine_assigned_to_id="",
                redmine_category_id="",
                redmine_fixed_version_id="",
            )
            result = ParsedResult(
                external_id="EMS1-7128",
                test_name="test_login",
                raw_status="Fail",
                status="f",
                duration_text="1s",
                duration_seconds=1.0,
            )
            context = {
                "project": {"name": "EMS"},
                "plan": {"name": "Regression"},
                "platform": {"name": "NetAtlas EMS"},
                "build": {"name": "03.00.11(AAVV.221)b5"},
            }

            with self.assertRaisesRegex(RedmineError, "fixed_version_id"):
                build_redmine_issue_payload(args, {}, Path("report.txt"), result, context)

    def test_custom_field_overrides_replace_template_values(self):
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "required_custom_fields": ["Severity"],
                        "custom_fields": [{"id": 10, "name": "Severity", "value": "Major"}],
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                redmine_project="",
                redmine_template=str(template_path),
                redmine_custom_fields=["Severity=Critical"],
                redmine_tracker_id="1",
                redmine_status_id="",
                redmine_priority_id="2",
                redmine_assigned_to_id="",
                redmine_category_id="",
                redmine_fixed_version_id="",
            )
            result = ParsedResult(
                external_id="EMS1-7128",
                test_name="test_login",
                raw_status="Fail",
                status="f",
                duration_text="1s",
                duration_seconds=1.0,
            )
            context = {
                "project": {"name": "EMS"},
                "plan": {"name": "Regression"},
                "platform": {"name": "NetAtlas EMS"},
                "build": {"name": "03.00.11(AAVV.221)b5"},
            }

            payload = build_redmine_issue_payload(args, {}, Path("report.txt"), result, context)

        self.assertEqual(payload["custom_fields"], [{"id": 10, "value": "Critical"}])

    def test_missing_required_template_custom_field_fails_before_redmine_call(self):
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "required_custom_fields": ["FW Ver"],
                        "custom_fields": [{"id": 11, "name": "FW Ver", "value": "{{header.EMS Version}}"}],
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(
                redmine_project="",
                redmine_template=str(template_path),
                redmine_custom_fields=None,
                redmine_tracker_id="1",
                redmine_status_id="",
                redmine_priority_id="2",
                redmine_assigned_to_id="",
                redmine_category_id="",
                redmine_fixed_version_id="",
            )
            result = ParsedResult(
                external_id="EMS1-7128",
                test_name="test_login",
                raw_status="Fail",
                status="f",
                duration_text="1s",
                duration_seconds=1.0,
            )
            context = {
                "project": {"name": "EMS"},
                "plan": {"name": "Regression"},
                "platform": {"name": "NetAtlas EMS"},
                "build": {"name": "03.00.11(AAVV.221)b5"},
            }

            with self.assertRaisesRegex(RedmineError, "FW Ver"):
                build_redmine_issue_payload(args, {}, Path("report.txt"), result, context)

    def test_blocks_manager_only_redmine_fields(self):
        args = Namespace(
            redmine_project="ems",
            redmine_tracker_id="1",
            redmine_status_id="",
            redmine_priority_id="2",
            redmine_assigned_to_id="123",
            redmine_category_id="",
            redmine_fixed_version_id="",
        )
        result = ParsedResult(
            external_id="PRJ-6682",
            test_name="test_get_port_by_devicename",
            raw_status="Fail",
            status="f",
            duration_text="0s",
            duration_seconds=0.0,
        )
        context = {
            "project": {"name": "EMS"},
            "plan": {"name": "Regression"},
            "platform": {"name": "NetAtlas EMS"},
            "build": {"name": "03.00.11(AAVV.221)b5"},
        }

        with self.assertRaises(RedmineError):
            build_redmine_issue_payload(args, {}, Path("report.txt"), result, context)

    def test_blocks_manager_only_redmine_fields_from_env(self):
        os.environ["REDMINE_FIXED_VERSION_ID"] = "9"
        args = Namespace(
            redmine_project="ems",
            redmine_tracker_id="1",
            redmine_status_id="",
            redmine_priority_id="2",
            redmine_category_id="",
        )
        result = ParsedResult(
            external_id="PRJ-6682",
            test_name="test_get_port_by_devicename",
            raw_status="Fail",
            status="f",
            duration_text="0s",
            duration_seconds=0.0,
        )
        context = {
            "project": {"name": "EMS"},
            "plan": {"name": "Regression"},
            "platform": {"name": "NetAtlas EMS"},
            "build": {"name": "03.00.11(AAVV.221)b5"},
        }

        with self.assertRaises(RedmineError):
            build_redmine_issue_payload(args, {}, Path("report.txt"), result, context)

    def test_manager_env_switch_allows_manager_only_redmine_fields(self):
        os.environ["REDMINE_ALLOW_MANAGER_FIELDS"] = "true"
        args = Namespace(
            redmine_project="ems",
            redmine_tracker_id="1",
            redmine_status_id="",
            redmine_priority_id="2",
            redmine_assigned_to_id="123",
            redmine_category_id="",
            redmine_fixed_version_id="9",
        )
        result = ParsedResult(
            external_id="PRJ-6682",
            test_name="test_get_port_by_devicename",
            raw_status="Fail",
            status="f",
            duration_text="0s",
            duration_seconds=0.0,
        )
        context = {
            "project": {"name": "EMS"},
            "plan": {"name": "Regression"},
            "platform": {"name": "NetAtlas EMS"},
            "build": {"name": "03.00.11(AAVV.221)b5"},
        }

        payload = build_redmine_issue_payload(args, {}, Path("report.txt"), result, context)

        self.assertEqual(payload["assigned_to_id"], 123)
        self.assertEqual(payload["fixed_version_id"], 9)

    def test_build_notes_includes_redmine_link(self):
        result = ParsedResult(
            external_id="PRJ-6682",
            test_name="test_get_port_by_devicename",
            raw_status="Fail",
            status="f",
            duration_text="0s",
            duration_seconds=0.0,
        )
        issue = RedmineIssue(
            id="12345",
            url="https://redmine.example.com/issues/12345",
            subject="[PRJ-6682] failed",
            reused=True,
        )

        notes = build_notes({}, Path("report.txt"), result, issue)

        self.assertIn("REDMINE-ID: #12345", notes)
        self.assertIn("REDMINE-URL: https://redmine.example.com/issues/12345", notes)
        self.assertIn("REDMINE-REUSED: yes", notes)

    def test_build_existing_redmine_issue_from_args(self):
        args = Namespace(
            redmine_issue_id="255162",
            redmine_issue_url="https://redmine.example.com/issues/255162",
        )
        result = ParsedResult(
            external_id="EMS1-7119",
            test_name="test_existing_bug_link",
            raw_status="Fail",
            status="f",
            duration_text="0s",
            duration_seconds=0.0,
        )

        issue = build_existing_redmine_issue(args, result)

        self.assertIsNotNone(issue)
        self.assertEqual(issue.id, "255162")
        self.assertEqual(issue.url, "https://redmine.example.com/issues/255162")
        self.assertTrue(issue.reused)


if __name__ == "__main__":
    unittest.main()
