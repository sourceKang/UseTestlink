from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from testlink_mcp.api import call_tool
from testlink_mcp.server import handle_request
from testlink_mcp.tools import TOOLS


class TestLinkMcpServerTests(unittest.TestCase):
    def test_initialize_advertises_pure_server_version(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        self.assertEqual("testlink-mcp", response["result"]["serverInfo"]["name"])
        self.assertEqual("2.1.0", response["result"]["serverInfo"]["version"])

    def test_tool_surface_excludes_redmine_and_upload_orchestration(self) -> None:
        tools = {tool["name"]: tool for tool in TOOLS}
        self.assertIn("testlink_report_execution", tools)
        self.assertIn("testlink_create_testcase", tools)
        self.assertIn("testlink_update_testcase", tools)
        self.assertNotIn("create_test_case", tools)
        self.assertNotIn("update_test_case", tools)
        self.assertNotIn("testlink_upload_report", tools)
        self.assertNotIn("report_result", tools)
        self.assertNotIn("report_results_batch", tools)
        for tool in TOOLS:
            for property_name in tool["inputSchema"].get("properties", {}):
                self.assertNotIn("redmine", property_name.casefold())
                self.assertNotIn("devkey", property_name.casefold())

    def test_protected_execution_defaults_to_preview(self) -> None:
        tool = next(tool for tool in TOOLS if tool["name"] == "testlink_report_execution")
        self.assertFalse(tool["inputSchema"]["properties"]["write"]["default"])
        self.assertIn("operation_id", tool["inputSchema"]["required"])
        self.assertIn("environment", tool["inputSchema"]["required"])
        self.assertIn("platform", tool["inputSchema"]["required"])

    def test_hidden_legacy_upload_tool_cannot_be_called_by_name(self) -> None:
        result = call_tool("testlink_upload_report", {})
        self.assertFalse(result["ok"])
        self.assertEqual("UnknownTool", result["error"]["type"])

    def test_testcase_tools_require_explicit_multi_row_authorization(self) -> None:
        tools = {tool["name"]: tool for tool in TOOLS}
        for name in ("testlink_create_testcase", "testlink_update_testcase"):
            schema = tools[name]["inputSchema"]
            self.assertTrue(schema["properties"]["single_step"]["default"])
            self.assertFalse(schema["properties"]["allow_multi_row"]["default"])
            self.assertIn("operation_id", schema["required"])
            self.assertIn("environment", schema["required"])
            self.assertGreaterEqual(len(schema["allOf"]), 2)

    def test_maintenance_toolset_exposes_only_relevant_surface(self) -> None:
        with patch.dict("os.environ", {"TESTLINK_MCP_TOOLSET": "maintenance"}):
            listed = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = {tool["name"] for tool in listed["result"]["tools"]}
            blocked = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "delete_execution", "arguments": {}},
                }
            )

        self.assertIn("testlink_create_testcase", names)
        self.assertIn("testlink_update_testcase", names)
        self.assertNotIn("delete_execution", names)
        self.assertEqual(-32602, blocked["error"]["code"])

    def test_single_call_resolver_returns_exact_execution_target(self) -> None:
        class FakeClient:
            def get_projects(self):
                return [{"id": "10", "name": "EMS"}]

            def get_project_test_plans(self, project_id):
                return [{"id": "20", "name": "Regression"}]

            def get_platforms(self, testplan_id):
                return [{"id": "30", "name": "Default Platform"}]

            def get_builds(self, testplan_id):
                return [{"id": "40", "name": "build-1"}]

            def get_plan_cases_by_external_id(self, testplan_id, platform_id):
                return {"EMS-1": {"id": "50", "name": "Login"}}

        arguments = {
            "operation_id": "resolve-1",
            "environment": "sandbox",
            "project": "EMS",
            "plan": "Regression",
            "platform": "Default Platform",
            "build": "build-1",
            "testcase_external_id": "EMS-1",
        }
        with (
            patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="sandbox")),
            patch("testlink_mcp.api.write_client", return_value=FakeClient()),
        ):
            result = call_tool("testlink_resolve_execution_target", arguments)

        self.assertTrue(result["ok"])
        self.assertEqual("40", result["result"]["target"]["build"]["id"])
        self.assertEqual("EMS-1", result["result"]["target"]["testcase"]["external_id"])

    def test_single_call_resolver_rejects_non_unique_exact_name(self) -> None:
        class DuplicateProjectClient:
            def get_projects(self):
                return [{"id": "10", "name": "EMS"}, {"id": "11", "name": "EMS"}]

        with (
            patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="sandbox")),
            patch("testlink_mcp.api.write_client", return_value=DuplicateProjectClient()),
        ):
            result = call_tool(
                "testlink_resolve_execution_target",
                {
                    "operation_id": "resolve-duplicate",
                    "environment": "sandbox",
                    "project": "EMS",
                    "plan": "Regression",
                    "platform": "Default Platform",
                    "build": "build-1",
                },
            )

        self.assertFalse(result["ok"])
        self.assertIn("found 2", result["error"]["error"]["message"])


if __name__ == "__main__":
    unittest.main()
