import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core.mcp_server import TOOLS, handle_request


class McpServerTests(unittest.TestCase):
    def test_tools_list_exposes_upload_report_as_preview_by_default(self):
        upload_tool = next(tool for tool in TOOLS if tool["name"] == "testlink_upload_report")

        self.assertEqual(upload_tool["inputSchema"]["properties"]["write"]["default"], False)
        self.assertIn("project", upload_tool["inputSchema"]["required"])
        self.assertIn("report", upload_tool["inputSchema"]["required"])
        self.assertIn("redmine_assigned_to_id", upload_tool["inputSchema"]["properties"])
        self.assertIn("redmine_fixed_version_id", upload_tool["inputSchema"]["properties"])
        self.assertIn(
            "Manager-only",
            upload_tool["inputSchema"]["properties"]["redmine_assigned_to_id"]["description"],
        )

    def test_tools_call_returns_mcp_content(self):
        with TemporaryDirectory() as tmpdir:
            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {
                        "name": "testlink_list_profiles",
                        "arguments": {"profiles": str(Path(tmpdir) / "profiles.json")},
                    },
                }
            )

        self.assertIsNotNone(response)
        self.assertEqual(response["id"], 7)
        self.assertFalse(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["profile_count"], 0)

    def test_initialize_advertises_tool_capability(self):
        response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        self.assertEqual(response["result"]["serverInfo"]["name"], "testlink-mcp")
        self.assertEqual(response["result"]["serverInfo"]["version"], "1.2.1")
        self.assertIn("tools", response["result"]["capabilities"])

    def test_tool_schemas_do_not_accept_url_or_devkey(self):
        for tool in TOOLS:
            properties = tool["inputSchema"]["properties"]
            self.assertNotIn("url", properties)
            self.assertNotIn("devkey", properties)

    def test_about_tool_is_exposed(self):
        about_tool = next(tool for tool in TOOLS if tool["name"] == "testlink_about")

        self.assertTrue(about_tool["annotations"]["readOnlyHint"])

    def test_phase1_tools_are_read_only(self):
        names = {
            "find_project",
            "find_test_plan",
            "list_test_suites",
            "list_test_cases",
            "get_test_case",
            "get_last_result",
            "get_builds",
        }
        tools_by_name = {tool["name"]: tool for tool in TOOLS}

        self.assertTrue(names.issubset(tools_by_name))
        for name in names:
            self.assertTrue(tools_by_name[name]["annotations"]["readOnlyHint"])

    def test_case_lookup_tools_require_one_case_identifier(self):
        tools_by_name = {tool["name"]: tool for tool in TOOLS}

        self.assertIn({"required": ["testcase_external_id"]}, tools_by_name["get_test_case"]["inputSchema"]["anyOf"])
        self.assertIn({"required": ["testcase_id"]}, tools_by_name["get_last_result"]["inputSchema"]["anyOf"])

    def test_phase2_phase3_tools_are_exposed(self):
        names = {
            "report_result",
            "report_results_batch",
            "create_build",
            "create_test_case",
            "update_test_case",
            "add_case_to_plan",
            "upload_attachment",
        }
        tools_by_name = {tool["name"]: tool for tool in TOOLS}

        self.assertTrue(names.issubset(tools_by_name))
        self.assertEqual(tools_by_name["report_result"]["inputSchema"]["properties"]["status"]["enum"], ["p", "f", "b"])
        self.assertFalse(tools_by_name["create_build"]["inputSchema"]["properties"]["write"]["default"])

    def test_phase4_tools_are_exposed_with_confirmation(self):
        tools_by_name = {tool["name"]: tool for tool in TOOLS}

        self.assertIn("delete_execution", tools_by_name)
        self.assertIn("overwrite_result", tools_by_name)
        self.assertIn("link_bug", tools_by_name)
        self.assertTrue(tools_by_name["delete_execution"]["annotations"]["destructiveHint"])
        self.assertTrue(tools_by_name["delete_execution"]["annotations"]["requiresConfirmation"])
        self.assertTrue(tools_by_name["overwrite_result"]["annotations"]["destructiveHint"])
        self.assertTrue(tools_by_name["overwrite_result"]["annotations"]["requiresConfirmation"])
        self.assertEqual(
            tools_by_name["delete_execution"]["inputSchema"]["properties"]["confirm"]["const"],
            True,
        )

if __name__ == "__main__":
    unittest.main()



