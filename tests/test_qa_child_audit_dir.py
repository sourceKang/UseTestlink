"""Child audits must not depend on the MCP process working directory.

Claude Desktop starts MCP servers with C:\\Windows\\System32 as the working
directory. The coordinator's children inherit it, so a relative default audit
path such as local/testlink_audit cannot be created there. These tests run the
real coordinator and real testlink-mcp/redmine-mcp children (network, runtime
and upstream clients replaced by synthetic stand-ins) with the children's
working directory set to one where "local" cannot be created, and check that
every audit lands under the caller's absolute audit_dir.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from qa_mcp_contracts import payload_digest
from qa_integration_agent.coordinator import QaCoordinator
from qa_integration_agent.errors import CoordinatorError
from qa_integration_agent.ports import StdioMcpPorts


FAKE_TESTLINK_CHILD = """
import os
from types import SimpleNamespace
from unittest.mock import patch
from testlink_agent_core.config import TestLinkSettings
import testlink_mcp.server as server

class Client:
    def get_projects(self): return [{"id": "10", "name": "EMS"}]
    def get_project_test_plans(self, project_id): return [{"id": "20", "name": "Regression"}]
    def get_builds(self, plan_id): return [{"id": "40", "name": "build-1"}]
    def get_platforms(self, plan_id): return [{"id": "30", "name": "Default Platform"}]
    def report_result(self, payload):
        with open(os.environ["FAKE_WRITE_LOG"], "a", encoding="utf-8") as handle:
            handle.write(str(payload.get("testcaseexternalid")) + "\\n")
        return [{"status": True, "operation": "reportTCResult", "message": "Success!", "id": 9001}]

runtime = SimpleNamespace(environment="sandbox", settings=TestLinkSettings(
    url="https://testlink.invalid", devkey="synthetic-devkey", timeout=60))
patch("socket.socket", side_effect=AssertionError("network forbidden")).start()
patch.object(server, "startup_health_check", return_value={"offline": True}).start()
patch("testlink_mcp.api.load_runtime", return_value=runtime).start()
patch("testlink_mcp.api.write_client", side_effect=lambda runtime: Client()).start()
raise SystemExit(server.main())
"""

FAKE_REDMINE_CHILD = """
import os
from types import SimpleNamespace
from unittest.mock import patch
import redmine_mcp.server as server

class Client:
    def add_comment(self, issue_id, notes):
        with open(os.environ["FAKE_WRITE_LOG"], "a", encoding="utf-8") as handle:
            handle.write("comment " + str(issue_id) + "\\n")
    def get_issue_journals(self, issue_id): return []

settings = SimpleNamespace(environment="sandbox", template_file=None)
patch("socket.socket", side_effect=AssertionError("network forbidden")).start()
patch.object(server, "startup_health_check", return_value={"offline": True}).start()
patch("redmine_mcp.api._runtime", return_value=(settings, Client())).start()
raise SystemExit(server.main())
"""

EXISTING_ISSUE = {
    "id": "12345",
    "url": "https://redmine.example.com/issues/12345",
    "subject": "Existing issue",
    "state": "open",
}


class ReusedIssuePorts(StdioMcpPorts):
    """Real TestLink and Redmine comment children; the Redmine bug decision is a reuse stub."""

    def redmine_bug(self, **kwargs: Any) -> dict[str, Any]:
        plan = {"operation_id": kwargs["operation_id"], "dedupe_marker": kwargs["dedupe_marker"], "action": "reuse"}
        if not kwargs.get("write"):
            return {"ok": True, "code": 0, "result": {
                "schema_version": "1.0",
                "operation_id": kwargs["operation_id"],
                "environment": kwargs["environment"],
                "mode": "preview",
                "preview_digest": payload_digest(plan),
                "planned_write": False,
                "action": "reuse",
                "dedupe_digest": hashlib.sha256(kwargs["dedupe_marker"].encode("utf-8")).hexdigest()[:16],
                "manager_fields_enabled": False,
                "blocked_fields": [],
                "warnings": [],
                "issue_fields": {},
                "issue_payload": {},
                "existing_issue": EXISTING_ISSUE,
            }}
        return {"ok": True, "code": 0, "result": {
            "schema_version": "1.0",
            "operation_id": kwargs["operation_id"],
            "environment": kwargs["environment"],
            "preview_digest": kwargs["preview_digest"],
            "status": "success",
            "action": "reused",
            "issue": {**EXISTING_ISSUE, "reused": True},
            "field_verification": {"status": "not-required-reused", "verified": None},
            "comment_status": "not-required",
            "audit_id": "redmine-bug-stub.json",
        }}


def write_report(directory: Path) -> Path:
    report = directory / "report.txt"
    report.write_text(
        "\n".join([
            "Report generated on: 2026-10-01",
            "Test Results:",
            "-------------",
            "[EMS-1][test_login] Result Fail (60s)",
            "[EMS-2][test_logout] Result Pass (30s)",
        ]),
        encoding="utf-8",
    )
    return report


class ChildAuditDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        # A file named "local" means "local/..." can never be created here,
        # the same failure as a read-only working directory such as System32.
        self.blocked_cwd = root / "blocked-cwd"
        self.blocked_cwd.mkdir()
        (self.blocked_cwd / "local").write_text("not a directory", encoding="utf-8")
        self.audit_dir = root / "caller" / "qa_audit"
        for name, source in (("testlink_child.py", FAKE_TESTLINK_CHILD), ("redmine_child.py", FAKE_REDMINE_CHILD)):
            (root / name).write_text(source, encoding="utf-8")
        for name in ("testlink.env", "redmine.env"):
            (root / name).write_text("PROFILE=sandbox\n", encoding="utf-8")
        self.write_log = root / "upstream-writes.log"
        self.write_log.touch()
        self.spawn_cwds: list[str | None] = []

    def writes(self) -> list[str]:
        return self.write_log.read_text(encoding="utf-8").split("\n")[:-1]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _fake_popen(self, real_popen):
        def fake_popen(command, **kwargs):
            script = "testlink_child.py" if command[-1] == "testlink_mcp.server" else "redmine_child.py"
            self.spawn_cwds.append(kwargs.get("cwd"))
            # Children inherit the coordinator's working directory; here it is unwritable.
            kwargs["cwd"] = str(self.blocked_cwd)
            return real_popen([os.sys.executable, str(self.root / script)], **kwargs)
        return fake_popen

    def _run(self, **execute_kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        ports = ReusedIssuePorts(
            testlink_env_file=str(self.root / "testlink.env"),
            redmine_env_file=str(self.root / "redmine.env"),
            timeout=60,
        )
        coordinator = QaCoordinator(ports)
        report = write_report(self.root)
        repo_root = str(Path(__file__).resolve().parents[1])
        with patch.dict(os.environ, {"PYTHONPATH": repo_root, "FAKE_WRITE_LOG": str(self.write_log)}), \
                patch("qa_integration_agent.ports.subprocess.Popen", side_effect=self._fake_popen(subprocess.Popen)):
            plan = coordinator.build_plan(
                operation_id="operation-blocked-cwd",
                environment="sandbox",
                project="EMS",
                plan="Regression",
                platform="Default Platform",
                build="build-1",
                report=str(report),
                redmine_create_bugs=True,
                redmine_project_id="ems",
                redmine_tracker_id="1",
                redmine_priority_id="2",
            )
            result = coordinator.execute_plan(
                plan,
                confirmed_preview_digest=plan["preview_digest"],
                report=str(report),
                **execute_kwargs,
            )
        return plan, result

    @staticmethod
    def _records(directory: Path) -> list[dict[str, Any]]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]

    def test_child_audits_land_under_the_callers_audit_dir_when_cwd_is_unwritable(self) -> None:
        _, result = self._run(audit_dir=str(self.audit_dir))

        self.assertEqual("completed", result["status"], result["audit"]["errors"])
        items = {item["testcase_external_id"]: item for item in result["audit"]["items"]}
        self.assertEqual({"success"}, {item["testlink_write"] for item in items.values()})
        self.assertEqual("added", items["EMS-1"]["evidence_comment"])
        self.assertEqual(["EMS-1", "EMS-2", "comment 12345"], sorted(self.writes()))

        testlink_records = self._records(self.audit_dir / "testlink")
        self.assertEqual(
            {(record["status"], record["testcase_external_id"]) for record in testlink_records},
            {("success", "EMS-1"), ("success", "EMS-2")},
        )
        self.assertEqual(
            {items["EMS-1"]["testlink_audit_id"], items["EMS-2"]["testlink_audit_id"]},
            {path.name for path in (self.audit_dir / "testlink").glob("*.json")},
        )
        comment_records = self._records(self.audit_dir / "redmine")
        self.assertEqual([("add-comment", "added")], [(r["action"], r["status"]) for r in comment_records])
        self.assertEqual(items["EMS-1"]["evidence_audit_id"], next((self.audit_dir / "redmine").glob("*.json")).name)
        self.assertEqual(1, len(list(self.audit_dir.glob("*-qa-workflow-*.json"))))
        # Nothing may leak into the working directory the children inherited.
        self.assertEqual(["local"], sorted(path.name for path in self.blocked_cwd.iterdir()))
        # The coordinator never pins a child cwd; correctness must not depend on it.
        self.assertEqual({None}, set(self.spawn_cwds))

    def test_resume_reuses_the_audit_directory_of_the_resumed_workflow(self) -> None:
        plan, first = self._run(audit_dir=str(self.audit_dir))
        workflow = next(self.audit_dir.glob("*-qa-workflow-*.json"))

        _, resumed = self._run(resume_audit=str(workflow))

        self.assertEqual("completed", resumed["status"], resumed["audit"]["errors"])
        self.assertEqual(
            {"skipped-resume"}, {item["testlink_write"] for item in resumed["audit"]["items"]}
        )
        self.assertEqual(2, len(list((self.audit_dir / "testlink").glob("*.json"))))
        # The resumed run found every completed item and wrote nothing upstream again.
        self.assertEqual(3, len(self.writes()))

    def test_unwritable_audit_dir_fails_before_any_external_write_with_its_absolute_path(self) -> None:
        unwritable = self.blocked_cwd / "local" / "qa_audit"

        with self.assertRaises(CoordinatorError) as context:
            self._run(audit_dir=str(unwritable))

        self.assertEqual("AUDIT_DIR_NOT_WRITABLE", context.exception.code)
        self.assertIn(os.path.abspath(unwritable), str(context.exception))
        self.assertEqual([], self.writes())


if __name__ == "__main__":
    unittest.main()
