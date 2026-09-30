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
        self.assertNotIn("cwd", run.call_args.kwargs)

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
        self.assertNotIn("cwd", run.call_args.kwargs)

    def test_missing_pointer_fails_before_subprocess(self) -> None:
        ports = StdioMcpPorts(testlink_env_file="", redmine_env_file="")
        with patch("qa_integration_agent.ports.subprocess.run") as run:
            with self.assertRaises(CoordinatorError) as context:
                ports.testlink_execution(operation_id="operation-missing")

        self.assertEqual("MCP_ENV_POINTER_REQUIRED", context.exception.code)
        run.assert_not_called()


# A synthetic line-delimited MCP child: no credentials, no network. It answers
# every tools/call with its pid and a per-process request counter so the tests
# can prove which process served which call.
FAKE_MCP_SERVER = """
import json, os, sys
count = 0
print("startup noise that is not JSON", flush=True)
for line in sys.stdin:
    request = json.loads(line)
    count += 1
    mode = os.environ.get("FAKE_MCP_MODE", "")
    if mode == "hang":
        continue
    if mode == "exit":
        sys.stderr.write("child failed TESTLINK_DEVKEY=synthetic-secret\\n")
        sys.stderr.flush()
        sys.exit(3)
    payload = {"ok": True, "code": 0, "result": {
        "pid": os.getpid(),
        "count": count,
        "batch_session": os.environ.get("TESTLINK_MCP_BATCH_SESSION"),
        "toolset": os.environ.get("TESTLINK_MCP_TOOLSET") or os.environ.get("REDMINE_MCP_TOOLSET"),
        "parent_env_leak": "TESTLINK_DEVKEY" in os.environ,
        "tool": request["params"]["name"],
        "arguments": request["params"]["arguments"],
    }}
    print(json.dumps({"jsonrpc": "2.0", "id": "other-request", "result": {}}), flush=True)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {
        "content": [{"type": "text", "text": json.dumps(payload)}]}}), flush=True)
"""


class StdioMcpPortsSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.server = root / "fake_mcp_server.py"
        self.server.write_text(FAKE_MCP_SERVER, encoding="utf-8")
        self.testlink_env = root / "testlink.env"
        self.redmine_env = root / "redmine.env"
        self.testlink_env.write_text("TESTLINK_AGENT_PROFILE=sandbox\n", encoding="utf-8")
        self.redmine_env.write_text("REDMINE_ENV=sandbox\n", encoding="utf-8")
        self.spawned: list[list[str]] = []
        real_popen = subprocess.Popen

        def fake_popen(command, **kwargs):
            self.spawned.append(list(command))
            return real_popen([os.sys.executable, str(self.server)], **kwargs)

        self.popen_patch = patch("qa_integration_agent.ports.subprocess.Popen", side_effect=fake_popen)
        self.popen_patch.start()

    def tearDown(self) -> None:
        self.popen_patch.stop()
        self.tmp.cleanup()

    def ports(self, timeout: int = 20) -> StdioMcpPorts:
        return StdioMcpPorts(
            testlink_env_file=str(self.testlink_env),
            redmine_env_file=str(self.redmine_env),
            timeout=timeout,
        )

    def test_session_reuses_one_child_per_server_for_the_whole_batch(self) -> None:
        ports = self.ports()
        with patch.dict(os.environ, {"TESTLINK_DEVKEY": "parent-secret"}), \
                patch("qa_integration_agent.ports.subprocess.run") as run:
            with ports.session():
                testlink = [ports.testlink_execution(operation_id=f"op-{index}") for index in range(25)]
                redmine = [ports.redmine_bug(operation_id=f"rm-{index}") for index in range(3)]

        run.assert_not_called()
        self.assertEqual(
            [[os.sys.executable, "-m", "testlink_mcp.server"], [os.sys.executable, "-m", "redmine_mcp.server"]],
            self.spawned,
        )
        self.assertEqual(1, len({item["result"]["pid"] for item in testlink}))
        self.assertEqual(list(range(1, 26)), [item["result"]["count"] for item in testlink])
        self.assertEqual([f"op-{index}" for index in range(25)],
                         [item["result"]["arguments"]["operation_id"] for item in testlink])
        self.assertEqual({"1"}, {item["result"]["batch_session"] for item in testlink})
        self.assertEqual({"integration"}, {item["result"]["toolset"] for item in testlink + redmine})
        self.assertEqual({False}, {item["result"]["parent_env_leak"] for item in testlink + redmine})
        # Only the TestLink child is told to reuse its resolution; Redmine keeps its own behaviour.
        self.assertEqual({None}, {item["result"]["batch_session"] for item in redmine})
        self.assertIsNone(ports._session_children)

    def test_calls_outside_a_session_still_use_one_shot_children(self) -> None:
        ports = self.ports()
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=mcp_stdout({"ok": True, "code": 0, "result": {}}), stderr="",
        )
        with patch("qa_integration_agent.ports.subprocess.run", return_value=completed) as run:
            with ports.session():
                pass
            ports.testlink_execution(operation_id="op-after-session")
            ports.testlink_execution(operation_id="op-after-session-2")

        self.assertEqual(2, run.call_count)
        self.assertNotIn("TESTLINK_MCP_BATCH_SESSION", run.call_args.kwargs["env"])
        self.assertEqual([], self.spawned)

    def test_timed_out_session_child_is_replaced_not_reused(self) -> None:
        ports = self.ports(timeout=1)
        with ports.session():
            with patch.dict(os.environ, {"FAKE_MCP_MODE": "hang"}):
                with self.assertRaises(CoordinatorError) as context:
                    ports.testlink_execution(operation_id="op-hang")
            recovered = ports.testlink_execution(operation_id="op-after-timeout")

        self.assertEqual("MCP_TIMEOUT", context.exception.code)
        self.assertTrue(context.exception.retryable)
        self.assertEqual(2, len(self.spawned))
        self.assertEqual(1, recovered["result"]["count"])

    def test_exited_session_child_reports_redacted_stderr(self) -> None:
        ports = self.ports()
        with patch.dict(os.environ, {"FAKE_MCP_MODE": "exit"}):
            with ports.session():
                with self.assertRaises(CoordinatorError) as context:
                    ports.testlink_execution(operation_id="op-exit")

        self.assertEqual("MCP_PROCESS_FAILED", context.exception.code)
        self.assertIn("code 3", str(context.exception))
        self.assertNotIn("synthetic-secret", str(context.exception))


# Runs the real testlink_mcp.server loop in the child, with the network, the
# runtime and the XML-RPC client replaced by synthetic stand-ins. The client
# logs every call so the parent can count authentications and name lookups.
REAL_TESTLINK_CHILD = """
import os, socket
from types import SimpleNamespace
from unittest.mock import patch
from testlink_agent_core.config import TestLinkSettings
import testlink_mcp.server as server

LOG = os.environ["FAKE_CALL_LOG"]

def log(name):
    with open(LOG, "a", encoding="utf-8") as handle:
        handle.write(name + "\\n")

class Client:
    def get_projects(self):
        log("get_projects"); return [{"id": "10", "name": "EMS"}]
    def get_project_test_plans(self, project_id):
        log("get_project_test_plans"); return [{"id": "20", "name": "Regression"}]
    def get_builds(self, plan_id):
        log("get_builds"); return [{"id": "40", "name": "build-1"}]
    def get_platforms(self, plan_id):
        log("get_platforms"); return [{"id": "30", "name": "Default Platform"}]

def write_client(runtime):
    log("check_devkey"); return Client()

runtime = SimpleNamespace(environment="sandbox", settings=TestLinkSettings(
    url="https://testlink.invalid", devkey="synthetic-devkey", timeout=60))
patch("socket.socket", side_effect=AssertionError("network forbidden")).start()
patch.object(server, "startup_health_check", return_value={"offline": True}).start()
patch("testlink_mcp.api.load_runtime", return_value=runtime).start()
patch("testlink_mcp.api.write_client", side_effect=write_client).start()
raise SystemExit(server.main())
"""


class StdioMcpPortsRealTestLinkSessionTests(unittest.TestCase):
    def test_real_testlink_child_resolves_the_target_once_per_session(self) -> None:
        cases = 12
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            script = root / "real_testlink_child.py"
            script.write_text(REAL_TESTLINK_CHILD, encoding="utf-8")
            call_log = root / "calls.log"
            testlink_env = root / "testlink.env"
            testlink_env.write_text("TESTLINK_AGENT_PROFILE=sandbox\n", encoding="utf-8")
            spawned: list[list[str]] = []
            real_popen = subprocess.Popen

            def fake_popen(command, **kwargs):
                spawned.append(list(command))
                return real_popen([os.sys.executable, str(script)], **kwargs)

            ports = StdioMcpPorts(testlink_env_file=str(testlink_env), redmine_env_file="", timeout=30)
            repo_root = str(Path(__file__).resolve().parents[1])
            with patch.dict(os.environ, {"FAKE_CALL_LOG": str(call_log), "PYTHONPATH": repo_root}), \
                    patch("qa_integration_agent.ports.subprocess.Popen", side_effect=fake_popen):
                with ports.session():
                    previews = [
                        ports.testlink_execution(
                            operation_id=f"operation-real-child-{index}",
                            environment="sandbox",
                            project="EMS",
                            plan="Regression",
                            platform="Default Platform",
                            build="build-1",
                            testcase_external_id=f"EMS-{index + 1}",
                            status="p",
                            notes=f"Case {index + 1}",
                            execution_duration=0.5,
                        )
                        for index in range(cases)
                    ]
            calls = call_log.read_text(encoding="utf-8").split()

        self.assertEqual(1, len(spawned))
        self.assertTrue(all(preview["ok"] for preview in previews), previews[0])
        self.assertEqual(cases, len({preview["result"]["preview_digest"] for preview in previews}))
        self.assertEqual(
            {"check_devkey": 1, "get_projects": 1, "get_project_test_plans": 1, "get_builds": 1, "get_platforms": 1},
            {name: calls.count(name) for name in sorted(set(calls))},
        )


if __name__ == "__main__":
    unittest.main()
