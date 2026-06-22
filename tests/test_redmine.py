import unittest
from argparse import Namespace
from pathlib import Path

from testlink_agent_core.models import ParsedResult, RedmineIssue
from testlink_agent_core.redmine import (
    build_existing_redmine_issue,
    build_notes,
    build_redmine_issue_payload,
)


class RedmineTests(unittest.TestCase):
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
        self.assertIn("[PRJ-6682]", payload["subject"])
        self.assertIn("Test case: PRJ-6682", payload["description"])
        self.assertIn("Platform: NetAtlas EMS", payload["description"])

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
