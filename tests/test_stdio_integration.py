"""Real local stdio processes, using only synthetic artifacts and no upstream calls."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import qa_mcp_contracts
from qa_mcp_contracts import payload_digest


class StdioIntegrationTests(unittest.TestCase):
    def test_all_servers_initialize_discover_ping_and_qa_reads_utf8(self):
        modules = (
            ("testlink_mcp.server", "testlink-mcp", "TESTLINK_MCP_TOOLSET", "execution", "testlink_report_execution"),
            ("redmine_mcp.server", "redmine-mcp", "REDMINE_MCP_TOOLSET", "issue", "redmine_create_bug"),
            ("qa_integration_agent.server", "qa-integration-agent", "QA_INTEGRATION_TOOLSET", "import", "qa_read_preview_artifact"),
        )
        with TemporaryDirectory() as directory:
            plan = {"operation_id": "stdio-offline", "items": [{"note": "繁體中文驗證"}],
                    "warnings": [], "ignored": [], "environment": "sandbox", "target": {"project": "離線測試"}}
            plan["preview_digest"] = payload_digest(plan)
            artifact = Path(directory) / "preview.json"
            artifact.write_text(json.dumps({"artifact_type": "qa-preview-plan", "schema_version": "1.0",
                                           "operation_id": plan["operation_id"], "preview_digest": plan["preview_digest"],
                                           "plan": plan, "review": {}}, ensure_ascii=False), encoding="utf-8")
            for module, name, key, toolset, expected_tool in modules:
                with self.subTest(server=name):
                    env = {k: v for k, v in os.environ.items()
                           if not k.startswith(("TESTLINK_", "REDMINE_", "QA_", "PYTHON"))}
                    env.update({"PYTHONUTF8": "1", key: toolset,
                                "PYTHONPATH": str(Path(qa_mcp_contracts.__file__).resolve().parent.parent)})
                    requests = [
                        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                            "protocolVersion": "2024-11-05", "capabilities": {},
                            "clientInfo": {"name": "offline-client", "version": "1"}}},
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
                    ]
                    if name == "qa-integration-agent":
                        requests.append({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                            "name": "qa_read_preview_artifact", "arguments": {
                                "operation_id": plan["operation_id"], "preview_artifact": str(artifact),
                                "preview_digest": plan["preview_digest"]}}})
                        requests.append({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
                            "name": "qa_execute_preview_artifact", "arguments": {
                                "operation_id": plan["operation_id"], "preview_artifact": str(artifact),
                                "preview_digest": plan["preview_digest"]}}})
                    bootstrap = (
                        "import importlib; from unittest.mock import patch; "
                        f"server = importlib.import_module({module!r}); "
                        "guard = patch('socket.socket', side_effect=AssertionError('network forbidden')); guard.start(); "
                        + ("health = patch.object(server, 'startup_health_check', return_value={'offline': True}); health.start(); "
                           if name != "qa-integration-agent" else "")
                        + "raise SystemExit(server.main())"
                    )
                    completed = subprocess.run([sys.executable, "-c", bootstrap],
                        input="".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests),
                        text=True, encoding="utf-8", capture_output=True, timeout=20, cwd=directory, env=env)
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    responses = [json.loads(line) for line in completed.stdout.splitlines()]
                    self.assertEqual([1, 2, 3, 4, 5] if name == "qa-integration-agent" else [1, 2, 3],
                                     [r["id"] for r in responses])
                    self.assertEqual(name, responses[0]["result"]["serverInfo"]["name"])
                    self.assertIn(expected_tool, {t["name"] for t in responses[1]["result"]["tools"]})
                    self.assertEqual({}, responses[2]["result"])
                    if name == "qa-integration-agent":
                        payload = json.loads(responses[3]["result"]["content"][0]["text"])
                        self.assertEqual("繁體中文驗證", payload["result"]["entries"][0]["note"])
                        self.assertTrue(responses[4]["result"]["isError"])
                        failure = json.loads(responses[4]["result"]["content"][0]["text"])
                        self.assertEqual("CONFIRMATION_REQUIRED", failure["error"]["error"]["code"])
