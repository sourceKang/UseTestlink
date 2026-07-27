from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from qa_integration_agent.errors import CoordinatorError
from qa_integration_agent.ports import StdioMcpPorts


def mcp_stdout(payload: dict) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "qa-tool",
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": not bool(payload.get("ok")),
            },
        }
    ) + "\n"


class StdioMcpPortsTests(unittest.TestCase):
    def test_testlink_child_receives_only_testlink_pointer(self) -> None:
        with TemporaryDirectory() as tmpdir:
            testlink_env = Path(tmpdir) / "testlink.env"
            redmine_env = Path(tmpdir) / "redmine.env"
            testlink_env.write_text("TESTLINK_AGENT_PROFILE=sandbox\n", encoding="utf-8")
            redmine_env.write_text("REDMINE_ENV=sandbox\n", encoding="utf-8")
            ports = StdioMcpPorts(
                testlink_env_file=str(testlink_env),
                redmine_env_file=str(redmine_env),
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=mcp_stdout({"ok": True, "code": 0, "result": {"mode": "preview"}}),
                stderr="",
            )
            with patch.dict(
                os.environ,
                {
                    "TESTLINK_DEVKEY": "parent-testlink-secret",
                    "REDMINE_API_KEY": "parent-redmine-secret",
                    "QA_TESTLINK_MCP_ENV_FILE": str(testlink_env),
                    "QA_REDMINE_MCP_ENV_FILE": str(redmine_env),
                },
                clear=False,
            ), patch("qa_integration_agent.ports.subprocess.run", return_value=completed) as run:
                result = ports.testlink_execution(operation_id="operation-testlink")

        child_env = run.call_args.kwargs["env"]
        self.assertTrue(result["ok"])
        self.assertEqual(str(testlink_env.resolve()), child_env["TESTLINK_MCP_ENV_FILE"])
        self.assertNotIn("TESTLINK_DEVKEY", child_env)
        self.assertNotIn("REDMINE_API_KEY", child_env)
        self.assertNotIn("REDMINE_MCP_ENV_FILE", child_env)
        self.assertNotIn("QA_REDMINE_MCP_ENV_FILE", child_env)
        self.assertEqual([os.sys.executable, "-m", "testlink_mcp.server"], run.call_args.args[0])
        self.assertEqual("integration", run.call_args.kwargs["env"]["TESTLINK_MCP_TOOLSET"])

    def test_redmine_child_receives_only_redmine_pointer(self) -> None:
        with TemporaryDirectory() as tmpdir:
            testlink_env = Path(tmpdir) / "testlink.env"
            redmine_env = Path(tmpdir) / "redmine.env"
            testlink_env.write_text("TESTLINK_AGENT_PROFILE=sandbox\n", encoding="utf-8")
            redmine_env.write_text("REDMINE_ENV=sandbox\n", encoding="utf-8")
            ports = StdioMcpPorts(
                testlink_env_file=str(testlink_env),
                redmine_env_file=str(redmine_env),
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=mcp_stdout({"ok": True, "code": 0, "result": {"action": "create"}}),
                stderr="",
            )
            with patch("qa_integration_agent.ports.subprocess.run", return_value=completed) as run:
                result = ports.redmine_bug(operation_id="operation-redmine")

        child_env = run.call_args.kwargs["env"]
        self.assertTrue(result["ok"])
        self.assertEqual(str(redmine_env.resolve()), child_env["REDMINE_MCP_ENV_FILE"])
        self.assertNotIn("TESTLINK_MCP_ENV_FILE", child_env)
        self.assertNotIn("QA_TESTLINK_MCP_ENV_FILE", child_env)
        self.assertEqual([os.sys.executable, "-m", "redmine_mcp.server"], run.call_args.args[0])
        self.assertEqual("integration", run.call_args.kwargs["env"]["REDMINE_MCP_TOOLSET"])

    def test_missing_pointer_fails_before_subprocess(self) -> None:
        ports = StdioMcpPorts(testlink_env_file="", redmine_env_file="")
        with patch("qa_integration_agent.ports.subprocess.run") as run:
            with self.assertRaises(CoordinatorError) as context:
                ports.testlink_execution(operation_id="operation-missing")

        self.assertEqual("MCP_ENV_POINTER_REQUIRED", context.exception.code)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
