import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from testlink_agent_core import api


class ApiTests(unittest.TestCase):
    def test_profile_roundtrip_returns_structured_payloads(self):
        with TemporaryDirectory() as tmpdir:
            profiles = str(Path(tmpdir) / "profiles.json")

            saved = api.save_profile(
                "gateway-vpn",
                project="Gateway",
                suite_id="695420",
                suite_name="VPN",
                profiles=profiles,
            )
            listed = api.list_profiles(profiles=profiles)
            deleted = api.delete_profile("gateway-vpn", profiles=profiles)

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["result"]["profile"], "gateway-vpn")
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["result"]["profile_count"], 1)
        self.assertEqual(listed["result"]["profiles"][0]["suite_id"], "695420")
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["result"]["removed"]["project"], "Gateway")

    def test_secret_arguments_are_redacted_from_errors(self):
        result = api.call_tool("missing-tool", {"devkey": "secret"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "UnknownTool")

    def test_phase1_find_project_uses_live_resolver(self):
        class FakeClient:
            def get_projects(self):
                return [{"id": "10", "name": "Gateway", "prefix": "GW"}]

        with patch.object(api, "_client", return_value=FakeClient()):
            result = api.call_tool("find_project", {"name": "Gateway"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["project"]["id"], "10")

    def test_phase1_get_builds_returns_structured_rows(self):
        class FakeClient:
            def get_projects(self):
                return [{"id": "10", "name": "Gateway"}]

            def get_project_test_plans(self, project_id):
                return [{"id": "20", "name": "Regression", "testproject_id": project_id}]

            def get_builds(self, testplan_id):
                return [
                    {"id": "30", "name": "old", "active": "1", "is_open": "1", "creation_ts": "2026-01-01"},
                    {"id": "31", "name": "closed", "active": "1", "is_open": "0", "creation_ts": "2026-02-01"},
                ]

        with patch.object(api, "_client", return_value=FakeClient()):
            result = api.call_tool(
                "get_builds",
                {"project": "Gateway", "plan": "Regression", "open_only": True},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["result"]["builds"]), 1)
        self.assertEqual(result["result"]["builds"][0]["id"], "30")

    def test_phase2_report_result_preview_builds_payload(self):
        class FakeClient:
            def get_projects(self):
                return [{"id": "10", "name": "Gateway"}]

            def get_project_test_plans(self, project_id):
                return [{"id": "20", "name": "Regression", "testproject_id": project_id}]

            def get_builds(self, testplan_id):
                return [{"id": "30", "name": "build-1"}]

            def get_platforms(self, testplan_id):
                return [{"id": "40", "name": "Windows"}]

        with patch.object(api, "_client", return_value=FakeClient()):
            result = api.call_tool(
                "report_result",
                {
                    "project": "Gateway",
                    "plan": "Regression",
                    "build": "build-1",
                    "platform": "Windows",
                    "testcase_external_id": "GW-1",
                    "status": "f",
                    "notes": "Assertion failed.",
                    "framework": "pytest",
                    "executed_at": "2026-06-24T10:00:00+08:00",
                    "failure_summary": "Expected 200, got 500.",
                },
            )

        self.assertTrue(result["ok"])
        payload = result["result"]["payload"]
        self.assertEqual(payload["status"], "f")
        self.assertEqual(payload["testplanid"], "20")
        self.assertEqual(payload["buildid"], "30")
        self.assertIn("Framework: pytest", payload["notes"])
        self.assertEqual(result["result"]["status_counts"]["fail"], 1)

    def test_phase2_batch_preview_counts_statuses(self):
        class FakeClient:
            def get_projects(self):
                return [{"id": "10", "name": "Gateway"}]

            def get_project_test_plans(self, project_id):
                return [{"id": "20", "name": "Regression", "testproject_id": project_id}]

        with patch.object(api, "_client", return_value=FakeClient()):
            result = api.call_tool(
                "report_results_batch",
                {
                    "project": "Gateway",
                    "plan": "Regression",
                    "build_id": "30",
                    "results": [
                        {"testcase_external_id": "GW-1", "status": "p", "notes": "ok"},
                        {"testcase_external_id": "GW-2", "status": "f", "notes": "bad"},
                        {"testcase_external_id": "GW-3", "status": "b", "notes": "blocked"},
                    ],
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["status_counts"], {"pass": 1, "fail": 1, "blocked": 1})
        self.assertEqual(result["result"]["total_count"], 3)

    def test_phase3_create_build_blocks_duplicate(self):
        class FakeClient:
            def get_projects(self):
                return [{"id": "10", "name": "Gateway"}]

            def get_project_test_plans(self, project_id):
                return [{"id": "20", "name": "Regression", "testproject_id": project_id}]

            def get_builds(self, testplan_id):
                return [{"id": "30", "name": "build-1"}]

        with patch.object(api, "_client", return_value=FakeClient()):
            result = api.call_tool("create_build", {"project": "Gateway", "plan": "Regression", "name": "build-1"})

        self.assertTrue(result["ok"])
        self.assertTrue(result["result"]["duplicate_found"])
        self.assertNotIn("response", result["result"])

    def test_phase3_create_test_case_blocks_duplicate_before_author_required(self):
        class FakeClient:
            def get_projects(self):
                return [{"id": "10", "name": "Gateway"}]

            def get_first_level_test_suites(self, project_id):
                return [{"id": "50", "name": "API"}]

            def get_child_test_suites(self, suite_id):
                return []

            def get_suite_cases(self, testsuite_id, deep=True, details="full"):
                return [{"id": "60", "name": "can_login", "full_external_id": "GW-60"}]

        with patch.object(api, "_client", return_value=FakeClient()):
            result = api.call_tool(
                "create_test_case",
                {
                    "project": "Gateway",
                    "suite_name": "API",
                    "name": "can_login",
                    "steps": ["Open page => Page opens"],
                },
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["result"]["duplicate_found"])

    def test_phase3_upload_attachment_preview_hides_content(self):
        class FakeClient:
            pass

        with TemporaryDirectory() as tmpdir:
            attachment = Path(tmpdir) / "evidence.txt"
            attachment.write_text("hello", encoding="utf-8")
            with patch.object(api, "_client", return_value=FakeClient()):
                result = api.call_tool(
                    "upload_attachment",
                    {
                        "attachment_type": "testcase",
                        "target_id": "123",
                        "file": str(attachment),
                    },
                )

        self.assertTrue(result["ok"])
        self.assertNotIn("content", result["result"]["payload"])
        self.assertEqual(result["result"]["payload"]["filename"], "evidence.txt")

    def test_phase4_delete_execution_requires_confirmation_for_write(self):
        class FakeClient:
            def delete_execution(self, execution_id):
                return {"deleted": execution_id}

        with patch.object(api, "_client", return_value=FakeClient()):
            result = api.call_tool("delete_execution", {"execution_id": "99", "write": True})

        self.assertFalse(result["ok"])
        self.assertIn("confirm=true", result["error"]["message"])

    def test_phase4_overwrite_result_requires_confirm(self):
        result = api.call_tool(
            "overwrite_result",
            {
                "project": "Gateway",
                "plan": "Regression",
                "build_id": "30",
                "testcase_external_id": "GW-1",
                "status": "f",
                "notes": "overwrite",
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "ConfirmationRequired")

    def test_phase4_link_bug_writes_bug_id_to_notes_only(self):
        class FakeClient:
            def get_projects(self):
                return [{"id": "10", "name": "Gateway"}]

            def get_project_test_plans(self, project_id):
                return [{"id": "20", "name": "Regression", "testproject_id": project_id}]

        with patch.object(api, "_client", return_value=FakeClient()):
            result = api.call_tool(
                "link_bug",
                {
                    "bug_id": "BUG-123",
                    "project": "Gateway",
                    "plan": "Regression",
                    "build_id": "30",
                    "testcase_external_id": "GW-1",
                    "status": "f",
                    "notes": "Known issue.",
                },
            )

        self.assertTrue(result["ok"])
        payload = result["result"]["payload"]
        self.assertIn("BUG-ID: BUG-123", payload["notes"])
        self.assertNotIn("bugid", payload)
        self.assertEqual(result["result"]["link_mode"], "notes")

if __name__ == "__main__":
    unittest.main()

