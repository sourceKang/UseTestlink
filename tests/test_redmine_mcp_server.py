from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from redmine_mcp import server
from redmine_mcp.server import handle_request
from redmine_mcp.tools import TOOLS


class RedmineMcpServerTests(unittest.TestCase):
    def test_initialize_advertises_redmine_server(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        self.assertEqual("redmine-mcp", response["result"]["serverInfo"]["name"])
        self.assertEqual("0.2.0", response["result"]["serverInfo"]["version"])

    def test_expected_tools_are_exposed(self) -> None:
        tools = {tool["name"]: tool for tool in TOOLS}
        self.assertEqual(
            {
                "redmine_health",
                "redmine_search_issues",
                "redmine_get_project_metadata",
                "redmine_validate_template",
                "redmine_preview_bug",
                "redmine_create_bug",
                "redmine_preview_comment",
                "redmine_add_comment",
            },
            set(tools),
        )
        self.assertFalse(tools["redmine_create_bug"]["inputSchema"]["properties"]["write"]["default"])
        self.assertFalse(tools["redmine_add_comment"]["inputSchema"]["properties"]["write"]["default"])
        attachments = tools["redmine_create_bug"]["inputSchema"]["properties"]["attachments"]
        self.assertEqual(5, attachments["maxItems"])
        self.assertEqual(["file"], attachments["items"]["required"])

    def test_tool_schemas_never_accept_api_credentials(self) -> None:
        for tool in TOOLS:
            properties = tool["inputSchema"]["properties"]
            normalized = {name.casefold().replace("_", "") for name in properties}
            self.assertNotIn("redmineapikey", normalized)
            self.assertNotIn("apikey", normalized)
            self.assertNotIn("authorization", normalized)

    def test_tools_call_redacts_secret_content(self) -> None:
        with patch.object(
            server,
            "call_tool",
            return_value={"ok": True, "code": 0, "result": {"api_key": "redmine-secret"}},
        ):
            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": "fake", "arguments": {}},
                }
            )

        text = response["result"]["content"][0]["text"]
        self.assertNotIn("redmine-secret", text)
        self.assertIn("*****", text)

    def test_tools_call_returns_structured_mcp_content(self) -> None:
        with patch.object(
            server,
            "call_tool",
            return_value={"ok": True, "code": 0, "result": {"server": "redmine-mcp"}},
        ):
            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "redmine_health", "arguments": {}},
                }
            )

        self.assertFalse(response["result"]["isError"])
        self.assertEqual("redmine-mcp", json.loads(response["result"]["content"][0]["text"])["result"]["server"])


if __name__ == "__main__":
    unittest.main()
