from __future__ import annotations

import unittest

from testlink_mcp.api import call_tool
from testlink_mcp.server import handle_request
from testlink_mcp.tools import TOOLS


class TestLinkMcpServerTests(unittest.TestCase):
    def test_initialize_advertises_pure_server_version(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        self.assertEqual("testlink-mcp", response["result"]["serverInfo"]["name"])
        self.assertEqual("2.0.0", response["result"]["serverInfo"]["version"])

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


if __name__ == "__main__":
    unittest.main()
