from __future__ import annotations

import unittest
from unittest.mock import patch

from qa_integration_agent.server import handle_request
from qa_integration_agent.tools import TOOLS


class QaIntegrationServerTests(unittest.TestCase):
    def test_initialize_advertises_coordinator(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        self.assertEqual("qa-integration-agent", response["result"]["serverInfo"]["name"])
        self.assertEqual("1.2.0", response["result"]["serverInfo"]["version"])

    def test_expected_tools_and_confirmation_contract(self) -> None:
        tools = {tool["name"]: tool for tool in TOOLS}
        self.assertEqual(
            {
                "qa_preview_report_artifact",
                "qa_preview_report_import",
                "qa_execute_preview_artifact",
                "qa_resume_preview_artifact",
                "qa_execute_report_import",
                "qa_resume_report_import",
                "qa_get_operation",
                "qa_validate_traceability",
                "qa_compare_shadow_previews",
            },
            set(tools),
        )
        self.assertEqual(True, tools["qa_execute_preview_artifact"]["inputSchema"]["properties"]["write"]["const"])
        self.assertIn("redmine_severity", tools["qa_preview_report_artifact"]["inputSchema"]["properties"])
        self.assertIn("redmine_custom_priority", tools["qa_preview_report_artifact"]["inputSchema"]["properties"])
        execute_properties = tools["qa_execute_preview_artifact"]["inputSchema"]["properties"]
        self.assertIn("preview_artifact", execute_properties)
        self.assertNotIn("report", execute_properties)
        self.assertNotIn("redmine_custom_fields", execute_properties)
        self.assertFalse(tools["qa_execute_preview_artifact"]["annotations"]["destructiveHint"])

    def test_coordinator_tool_schema_has_no_credentials_or_manager_fields(self) -> None:
        for tool in TOOLS:
            properties = tool["inputSchema"]["properties"]
            for name in properties:
                normalized = name.casefold().replace("_", "")
                self.assertNotIn("apikey", normalized)
                self.assertNotIn("devkey", normalized)
                self.assertNotIn("assignedto", normalized)
                self.assertNotIn("fixedversion", normalized)

    def test_default_import_toolset_omits_shadow_comparison(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("QA_INTEGRATION_TOOLSET", None)
            listed = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in listed["result"]["tools"]}

        self.assertIn("qa_preview_report_artifact", names)
        self.assertNotIn("qa_preview_report_import", names)
        self.assertIn("qa_execute_preview_artifact", names)
        self.assertNotIn("qa_execute_report_import", names)
        self.assertNotIn("qa_compare_shadow_previews", names)

    def test_shadow_toolset_includes_inline_preview_and_comparison(self) -> None:
        with patch.dict("os.environ", {"QA_INTEGRATION_TOOLSET": "shadow"}):
            listed = handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        names = {tool["name"] for tool in listed["result"]["tools"]}

        self.assertEqual({"qa_preview_report_import", "qa_compare_shadow_previews"}, names)


if __name__ == "__main__":
    unittest.main()
