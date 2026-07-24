from __future__ import annotations

import unittest

from qa_integration_agent.server import handle_request
from qa_integration_agent.tools import TOOLS


class QaIntegrationServerTests(unittest.TestCase):
    def test_initialize_advertises_coordinator(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        self.assertEqual("qa-integration-agent", response["result"]["serverInfo"]["name"])
        self.assertEqual("1.1.0", response["result"]["serverInfo"]["version"])

    def test_expected_tools_and_confirmation_contract(self) -> None:
        tools = {tool["name"]: tool for tool in TOOLS}
        self.assertEqual(
            {
                "qa_preview_report_import",
                "qa_execute_report_import",
                "qa_resume_report_import",
                "qa_get_operation",
                "qa_validate_traceability",
                "qa_compare_shadow_previews",
            },
            set(tools),
        )
        self.assertEqual(True, tools["qa_execute_report_import"]["inputSchema"]["properties"]["write"]["const"])
        self.assertIn("redmine_severity", tools["qa_preview_report_import"]["inputSchema"]["properties"])
        self.assertIn("redmine_custom_priority", tools["qa_preview_report_import"]["inputSchema"]["properties"])
        self.assertFalse(tools["qa_execute_report_import"]["annotations"]["destructiveHint"])

    def test_coordinator_tool_schema_has_no_credentials_or_manager_fields(self) -> None:
        for tool in TOOLS:
            properties = tool["inputSchema"]["properties"]
            for name in properties:
                normalized = name.casefold().replace("_", "")
                self.assertNotIn("apikey", normalized)
                self.assertNotIn("devkey", normalized)
                self.assertNotIn("assignedto", normalized)
                self.assertNotIn("fixedversion", normalized)


if __name__ == "__main__":
    unittest.main()
