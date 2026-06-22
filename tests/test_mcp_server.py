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

        self.assertEqual(response["result"]["serverInfo"]["name"], "testlink-agent")
        self.assertIn("tools", response["result"]["capabilities"])


if __name__ == "__main__":
    unittest.main()
