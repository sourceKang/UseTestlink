import unittest
import contextlib
import io
import json
import os
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from testlink_agent_core.commands import command_upload_report
from testlink_agent_core.errors import RedmineError
from testlink_agent_core.models import ParsedResult, RedmineIssue
from testlink_agent_core.audit import append_audit_result, build_audit_record, finalize_audit_record, write_audit_record
from testlink_agent_core.redmine import (
    RedmineClient,
    build_existing_redmine_issue,
    build_notes,
    build_redmine_evidence_comment,
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
        self.assertIn("Dedupe Key: testlink-agent:", payload["description"])

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

    def test_redmine_template_blank_fixed_version_is_omitted(self):
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

        self.assertNotIn("fixed_version_id", payload)

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

    def test_build_notes_includes_redmine_link_and_dedupe_marker(self):
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
            dedupe_marker="testlink-agent:abc123",
        )

        notes = build_notes({}, Path("report.txt"), result, issue, issue.dedupe_marker)

        self.assertIn("REDMINE-ID: #12345", notes)
        self.assertIn("REDMINE-URL: https://redmine.example.com/issues/12345", notes)
        self.assertIn("REDMINE-REUSED: yes", notes)
        self.assertIn("Dedupe Key: testlink-agent:abc123", notes)

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

    def test_build_redmine_evidence_comment_does_not_change_status_fields(self):
        result = ParsedResult(
            external_id="EMS1-7128",
            test_name="test_login",
            raw_status="Pass",
            status="p",
            duration_text="1s",
            duration_seconds=1.0,
            testlink_name="Login",
        )
        context = {
            "project": {"name": "EMS"},
            "plan": {"name": "Regression"},
            "platform": {"name": "NetAtlas EMS"},
            "build": {"name": "build-1"},
        }

        comment = build_redmine_evidence_comment(
            {"Report generated on": "2026-07-06"},
            Path("report.txt"),
            result,
            context,
            dedupe_marker_value="testlink-agent:abc123",
            testlink_response={"execution_id": "9001"},
        )

        self.assertIn("Automation retest evidence", comment)
        self.assertIn("Result: Pass", comment)
        self.assertIn("TestLink execution ID: 9001", comment)
        self.assertIn("Dedupe Key: testlink-agent:abc123", comment)
        self.assertIn("does not change Redmine status", comment)
        self.assertNotIn("status_id", comment)
        self.assertNotIn("assigned_to_id", comment)
        self.assertNotIn("fixed_version_id", comment)

    def test_add_issue_comment_uses_notes_only_payload(self):
        class FakeRedmineClient(RedmineClient):
            def __init__(self):
                super().__init__("https://redmine.example.com", "api-key")
                self.calls = []

            def request_json(self, method, path, payload=None, query=None):
                self.calls.append((method, path, payload, query))
                return {}

        client = FakeRedmineClient()

        response = client.add_issue_comment("12345", "Retest evidence")

        self.assertEqual(response, {})
        self.assertEqual(
            client.calls,
            [
                (
                    "PUT",
                    "/issues/12345.json",
                    {"issue": {"notes": "Retest evidence"}},
                    None,
                )
            ],
        )

    def test_upload_report_comments_on_reused_redmine_issue(self):
        class FakeTestLinkClient:
            def get_project_by_name(self, name):
                return {"id": "10", "name": name}

            def get_test_plan_by_name(self, project, plan):
                return {"id": "20", "name": plan}

            def get_platform_by_name(self, plan_id, platform):
                return {"id": "30", "name": platform}

            def get_build(self, plan_id, build, build_id):
                return {"id": "40", "name": build, "active": "1", "is_open": "1"}

            def get_plan_cases_by_external_id(self, plan_id, platform_id):
                return {"PRJ-6682": {"id": "50", "version": "1", "name": "Login"}}

            def report_result(self, params):
                return {"execution_id": "9001", "status": params["status"]}

        class FakeRedmineClient:
            def __init__(self):
                self.comments = []

            def find_open_issue_by_dedupe_marker(self, project_id, marker, tracker_id=None):
                return RedmineIssue(
                    id="12345",
                    url="https://redmine.example.com/issues/12345",
                    subject="[PRJ-6682] failed",
                    reused=True,
                    dedupe_marker=marker,
                )

            def find_open_issue_by_subject(self, project_id, subject, tracker_id=None):
                return None

            def create_issue(self, issue_payload):
                raise AssertionError("create_issue should not be called for reused issues")

            def add_issue_comment(self, issue_id, notes):
                self.comments.append((issue_id, notes))
                return {"commented": True}

        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report.txt"
            report.write_text(
                """Report generated on: 2026-07-06
EMS Version: 1.2.3
Test Results:
-------------
[PRJ-6682][test_login] Result Fail (1s)
""",
                encoding="utf-8",
            )
            fake_redmine = FakeRedmineClient()
            args = Namespace(
                env_file=None,
                timeout=60,
                project="EMS",
                plan="Regression",
                platform="NetAtlas EMS",
                build="build-1",
                build_id=None,
                report=str(report),
                skip_policy="ignore",
                write=True,
                require_open_build=True,
                progress=0,
                throttle=0,
                audit_dir=tmpdir,
                redmine_create_bugs=True,
                redmine_url="https://redmine.example.com",
                redmine_api_key="api-key",
                redmine_project="ems",
                redmine_template=None,
                redmine_custom_fields=None,
                redmine_tracker_id="1",
                redmine_status_id="",
                redmine_priority_id="2",
                redmine_assigned_to_id="",
                redmine_category_id="",
                redmine_fixed_version_id="",
                redmine_issue_id=None,
                redmine_issue_url=None,
                redmine_dedupe="open",
                testlink_bug_link="notes",
            )

            stdout = io.StringIO()
            with patch("testlink_agent_core.commands.parse_common_env", return_value=FakeTestLinkClient()):
                with patch("testlink_agent_core.commands.parse_redmine_client", return_value=fake_redmine):
                    with contextlib.redirect_stdout(stdout):
                        code = command_upload_report(args)
            output = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(len(fake_redmine.comments), 1)
        self.assertEqual(fake_redmine.comments[0][0], "12345")
        self.assertIn("Automation retest evidence", fake_redmine.comments[0][1])
        self.assertIn("TestLink execution ID: 9001", fake_redmine.comments[0][1])
        self.assertEqual(output["success_count"], 1)
        self.assertEqual(output["failure_count"], 0)

    def test_upload_report_resume_skips_completed_testlink_write_and_retries_comment(self):
        class FakeTestLinkClient:
            def get_project_by_name(self, name):
                return {"id": "10", "name": name}

            def get_test_plan_by_name(self, project, plan):
                return {"id": "20", "name": plan}

            def get_platform_by_name(self, plan_id, platform):
                return {"id": "30", "name": platform}

            def get_build(self, plan_id, build, build_id):
                return {"id": "40", "name": build, "active": "1", "is_open": "1"}

            def get_plan_cases_by_external_id(self, plan_id, platform_id):
                return {"PRJ-6682": {"id": "50", "version": "1", "name": "Login"}}

            def report_result(self, params):
                raise AssertionError("report_result should be skipped during resume")

        class FakeRedmineClient:
            def __init__(self):
                self.comments = []

            def add_issue_comment(self, issue_id, notes):
                self.comments.append((issue_id, notes))
                return {"commented": True}

        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report.txt"
            report.write_text(
                """Report generated on: 2026-07-06
EMS Version: 1.2.3
Test Results:
-------------
[PRJ-6682][test_login] Result Fail (1s)
""",
                encoding="utf-8",
            )
            target = {
                "project": "EMS",
                "testplan": "Regression",
                "testplanid": "20",
                "platform": "NetAtlas EMS",
                "platformid": "30",
                "build": "build-1",
                "buildid": "40",
            }
            resume_record = build_audit_record(
                operation="upload-report",
                mode="write",
                report_path=report,
                profile={"testlink": "corp", "redmine": "corp"},
                testlink_target=target,
                redmine_target={"enabled": True},
                report_schema="legacy-web-ems-report-v1",
                parsed_count=1,
                write_count=1,
                started_at="2026-07-06T01:02:03+00:00",
            )
            append_audit_result(
                resume_record,
                {
                    "external_id": "PRJ-6682",
                    "status": "f",
                    "redmine_action": "reused",
                    "redmine_issue": {
                        "id": "12345",
                        "url": "https://redmine.example.com/issues/12345",
                        "subject": "[PRJ-6682] failed",
                        "reused": True,
                        "dedupe_marker": "testlink-agent:abc123",
                    },
                    "redmine_comment": "failed",
                    "testlink_write": "success",
                    "testlink_response": {"execution_id": "9001"},
                    "errors": [{"stage": "redmine-comment"}],
                },
            )
            finalize_audit_record(resume_record, status="failed")
            resume_path = write_audit_record(resume_record, tmpdir)
            fake_redmine = FakeRedmineClient()
            args = Namespace(
                env_file=None,
                timeout=60,
                project="EMS",
                plan="Regression",
                platform="NetAtlas EMS",
                build="build-1",
                build_id=None,
                report=str(report),
                skip_policy="ignore",
                write=True,
                require_open_build=True,
                progress=0,
                throttle=0,
                audit_dir=tmpdir,
                resume_audit=str(resume_path),
                redmine_create_bugs=True,
                redmine_url="https://redmine.example.com",
                redmine_api_key="api-key",
                redmine_project="ems",
                redmine_template=None,
                redmine_custom_fields=None,
                redmine_tracker_id="1",
                redmine_status_id="",
                redmine_priority_id="2",
                redmine_assigned_to_id="",
                redmine_category_id="",
                redmine_fixed_version_id="",
                redmine_issue_id=None,
                redmine_issue_url=None,
                redmine_dedupe="open",
                testlink_bug_link="notes",
            )

            stdout = io.StringIO()
            with patch("testlink_agent_core.commands.parse_common_env", return_value=FakeTestLinkClient()):
                with patch("testlink_agent_core.commands.parse_redmine_client", return_value=fake_redmine):
                    with contextlib.redirect_stdout(stdout):
                        code = command_upload_report(args)
            output = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(output["success_count"], 0)
        self.assertEqual(output["resumed_count"], 1)
        self.assertEqual(output["failure_count"], 0)
        self.assertEqual(len(fake_redmine.comments), 1)
        self.assertIn("TestLink execution ID: 9001", fake_redmine.comments[0][1])


if __name__ == "__main__":
    unittest.main()
