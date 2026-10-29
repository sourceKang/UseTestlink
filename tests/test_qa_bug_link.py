"""testlink_bug_link (on by default with Redmine bug creation): failed results carry their bug as a TestLink link."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from qa_integration_agent import api
from qa_integration_agent.coordinator import TESTLINK_BUG_LINK_WARNING, QaCoordinator
from qa_integration_agent.errors import CoordinatorError
from qa_mcp_contracts import payload_digest
from tests.test_contracts import V2_CONTRACT_DIR, ContractPorts, load_schemas, schema_errors, write_node_report
from tests.test_qa_coordinator import FakePorts, workflow_args


def write_mixed_report(directory: str) -> Path:
    report = Path(directory) / "report.txt"
    report.write_text(
        "\n".join(
            [
                "Report generated on: 2026-07-13",
                "Test Results:",
                "-------------",
                "[EMS-1][test_login] Result Fail (60s)",
                "[EMS-2][test_logout] Result Pass (30s)",
            ]
        ),
        encoding="utf-8",
    )
    return report


class LinkPortMixin:
    """The testlink_link_bug port of the coordinator's TestLink child, offline."""

    def __init__(self, *, link_failures: int = 0, link_target_build: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.link_failures = link_failures
        self.link_target_build = link_target_build
        self.link_previews: list[dict] = []
        self.link_writes: list[dict] = []

    def testlink_link_bug(self, **kwargs):
        planned = {key: value for key, value in kwargs.items() if key not in {"write", "preview_digest", "audit_dir"}}
        digest = payload_digest(planned)
        # Same exact project/plan/platform/build resolution as testlink_report_execution.
        execution_preview = self.testlink_execution(
            **{key: kwargs[key] for key in ("operation_id", "environment", "project", "plan", "platform", "build",
                                            "testcase_external_id", "status", "notes")}
        )["result"]
        target = {
            key: dict(execution_preview["target"][key]) for key in ("project", "plan", "platform", "build")
        }
        if self.link_target_build:
            target["build"]["name"] = self.link_target_build
        target["testcase"] = {"external_id": kwargs["testcase_external_id"], "id": "50", "name": "Login"}
        if not kwargs.get("write"):
            self.link_previews.append(planned)
            return {
                "ok": True,
                "code": 0,
                "result": {"mode": "preview", "preview_digest": digest, "planned_write": True, "target": target},
            }
        self.link_writes.append(dict(kwargs))
        if self.link_failures:
            self.link_failures -= 1
            return {"ok": False, "code": 1, "error": {"error": {"code": "HTTP_ERROR", "message": "offline failure"}}}
        return {
            "ok": True,
            "code": 0,
            "result": {
                "status": "submitted",
                "verification_status": "link_unverified",
                "testcase_external_id": kwargs["testcase_external_id"],
                "bug_ids": kwargs["bug_ids"],
                "execution_readback": {"checked": True, "found": True, "execution_id": "9100", "matches": True},
                "audit_id": "link-audit.json",
            },
        }


class LinkPorts(LinkPortMixin, FakePorts):
    pass


class ContractLinkPorts(LinkPortMixin, ContractPorts):
    pass


class QaBugLinkPlanTests(unittest.TestCase):
    def test_flag_requires_redmine_bug_creation_and_a_boolean(self) -> None:
        coordinator = QaCoordinator(LinkPorts())
        with TemporaryDirectory() as tmpdir:
            report = write_mixed_report(tmpdir)
            for overrides in ({"testlink_bug_link": True, "redmine_create_bugs": False}, {"testlink_bug_link": "yes"}):
                with self.subTest(overrides=overrides), self.assertRaises(CoordinatorError) as raised:
                    coordinator.build_plan(**workflow_args(report, **overrides))
                self.assertEqual("INVALID_ARGUMENT", raised.exception.code)

    def test_unset_flag_follows_redmine_bug_creation(self) -> None:
        coordinator = QaCoordinator(LinkPorts())
        with TemporaryDirectory() as tmpdir:
            report = write_mixed_report(tmpdir)
            arguments = workflow_args(report)
            del arguments["testlink_bug_link"]
            with_bugs = coordinator.build_plan(**arguments)
            explicit_on = coordinator.build_plan(**workflow_args(report, testlink_bug_link=True))
            no_bugs = coordinator.build_plan(**{**arguments, "redmine_create_bugs": False})

        self.assertTrue(with_bugs["testlink_bug_link"])
        self.assertEqual(explicit_on["preview_digest"], with_bugs["preview_digest"])
        self.assertNotIn("testlink_bug_link", no_bugs)

    def test_explicit_off_keeps_the_earlier_shape_and_digest(self) -> None:
        coordinator = QaCoordinator(LinkPorts())
        with TemporaryDirectory() as tmpdir:
            report = write_mixed_report(tmpdir)
            explicit_off = coordinator.build_plan(**workflow_args(report, testlink_bug_link=False))

        self.assertNotIn("testlink_bug_link", explicit_off)
        self.assertNotIn("testlink_bug_link", QaCoordinator.public_preview(explicit_off))
        self.assertNotIn(TESTLINK_BUG_LINK_WARNING, explicit_off["warnings"])

    def test_enabled_plan_is_visible_in_the_preview_and_writes_nothing(self) -> None:
        ports = LinkPorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_mixed_report(tmpdir)
            explicit_off = coordinator.build_plan(**workflow_args(report, testlink_bug_link=False))
            plan = coordinator.build_plan(**workflow_args(report, testlink_bug_link=True))

        self.assertTrue(plan["testlink_bug_link"])
        self.assertNotEqual(explicit_off["preview_digest"], plan["preview_digest"])
        self.assertIn(TESTLINK_BUG_LINK_WARNING, plan["warnings"])
        preview = QaCoordinator.public_preview(plan)
        self.assertTrue(preview["testlink_bug_link"])
        modes = {item["testcase_external_id"]: item["testlink_mode"] for item in preview["items"]}
        self.assertEqual({"EMS-1": "link-bug", "EMS-2": "report-execution"}, modes)
        self.assertEqual([], ports.link_previews + ports.link_writes)
        self.assertEqual(0, ports.redmine_write_count + ports.testlink_write_count)


class QaBugLinkExecuteTests(unittest.TestCase):
    def execute(self, ports: LinkPorts, tmpdir: str, **overrides):
        coordinator = QaCoordinator(ports)
        report = write_mixed_report(tmpdir)
        plan = coordinator.build_plan(**workflow_args(report, testlink_bug_link=True, **overrides))
        result = coordinator.execute_plan(
            plan, confirmed_preview_digest=plan["preview_digest"], report=str(report), audit_dir=tmpdir
        )
        return coordinator, plan, report, result

    def test_failed_item_is_written_once_with_its_bug_and_passed_item_as_before(self) -> None:
        ports = LinkPorts(redmine_action="create")
        with TemporaryDirectory() as tmpdir:
            _, _, _, result = self.execute(ports, tmpdir)

        self.assertEqual("completed", result["status"])
        self.assertEqual(1, ports.redmine_write_count)
        self.assertEqual(1, len(ports.link_writes))
        written = ports.link_writes[0]
        self.assertEqual("EMS-1", written["testcase_external_id"])
        self.assertEqual(["12345"], written["bug_ids"])
        self.assertEqual("f", written["status"])
        self.assertEqual(1, written["execution_duration"])
        self.assertIn("REDMINE-ID: #12345", written["notes"])
        self.assertIn("audit_dir", written)
        self.assertEqual(1, ports.testlink_write_count)  # EMS-2 (Pass) still uses report_execution
        items = {item["testcase_external_id"]: item for item in result["audit"]["items"]}
        self.assertEqual("link-bug", items["EMS-1"]["testlink_mode"])
        self.assertEqual("link_unverified", items["EMS-1"]["testlink_link_status"])
        self.assertEqual("9100", items["EMS-1"]["testlink_execution_id"])
        self.assertNotIn("testlink_mode", items["EMS-2"])
        self.assertTrue(QaCoordinator.validate_traceability(result["audit"])["valid"])
        self.assertTrue(result["audit"]["workflow"]["testlink_bug_link"])

    def test_reused_bug_is_linked_and_still_gets_the_evidence_comment(self) -> None:
        ports = LinkPorts(redmine_action="reuse")
        with TemporaryDirectory() as tmpdir:
            _, _, _, result = self.execute(ports, tmpdir)

        self.assertEqual("completed", result["status"])
        self.assertEqual(["12345"], ports.link_writes[0]["bug_ids"])
        item = next(item for item in result["audit"]["items"] if item["testcase_external_id"] == "EMS-1")
        self.assertEqual("added", item["evidence_comment"])
        self.assertEqual(1, ports.comment_write_count)

    def test_changed_target_stops_before_the_link_write(self) -> None:
        ports = LinkPorts(link_target_build="build-2")
        with TemporaryDirectory() as tmpdir:
            _, _, _, result = self.execute(ports, tmpdir)

        self.assertEqual("partial-failure", result["status"])
        self.assertEqual([], ports.link_writes)
        item = next(item for item in result["audit"]["items"] if item["testcase_external_id"] == "EMS-1")
        self.assertEqual("failed", item["testlink_write"])
        self.assertEqual("TARGET_CHANGED", item["errors"][0]["code"])

    def test_failed_link_resumes_with_the_same_issue_and_child_operation(self) -> None:
        ports = LinkPorts(redmine_action="create", link_failures=1)
        with TemporaryDirectory() as tmpdir:
            coordinator, plan, report, first = self.execute(ports, tmpdir)
            self.assertEqual("partial-failure", first["status"])
            resume_plan = coordinator.build_plan(**workflow_args(report, testlink_bug_link=True))
            resumed = coordinator.execute_plan(
                resume_plan,
                confirmed_preview_digest=plan["preview_digest"],
                report=str(report),
                resume_audit=str(Path(tmpdir) / first["audit_id"]),
            )

        self.assertEqual("completed", resumed["status"])
        self.assertEqual(1, ports.redmine_write_count)
        self.assertEqual(2, len(ports.link_writes))
        self.assertEqual(ports.link_writes[0]["operation_id"], ports.link_writes[1]["operation_id"])
        self.assertEqual(ports.link_writes[0]["bug_ids"], ports.link_writes[1]["bug_ids"])


class TargetDigestCompatibilityTests(unittest.TestCase):
    def test_report_execution_and_link_bug_resolve_the_same_target_digest(self) -> None:
        from testlink_mcp import api as testlink_api
        from tests.test_testlink_link_bug import FakeTargetClient
        from tests.test_testlink_mcp import legacy_preview

        execution_target = testlink_api._build_plan("operation-x", "sandbox", legacy_preview())["target"]
        link_target = testlink_api._resolve_exact_target(
            FakeTargetClient(),
            project="EMS",
            plan="Regression",
            platform="Default Platform",
            build="build-1",
            testcase_external_id="EMS-1",
        )
        subset = {key: link_target[key] for key in ("project", "plan", "platform", "build")}
        self.assertEqual(payload_digest(execution_target), payload_digest(subset))


class QaBugLinkContractTests(unittest.TestCase):
    def test_public_api_passes_the_flag_and_outputs_match_v2_contracts(self) -> None:
        schemas = load_schemas(V2_CONTRACT_DIR)
        preview_schema = schemas["qa-report-preview.schema.json"]
        audit_schema = schemas["workflow-audit.schema.json"]
        ports = ContractLinkPorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = write_node_report(root, "node-a.txt", "OLT-A", ["[EMS-1][test_login] Result Pass (60s)"])
            second = write_node_report(root, "node-b.txt", "OLT-B", ["[EMS-1][test_login] Result Fail (75s)"])
            arguments = {
                "operation_id": "operation-bug-link-v2",
                "environment": "sandbox",
                "project": "EMS",
                "plan": "Regression",
                "platform": "Default Platform",
                "build": "build-1",
                "reports": [{"label": "node-a", "path": str(first)}, {"label": "node-b", "path": str(second)}],
                "redmine_create_bugs": True,
                "redmine_project_id": "ems",
                "testlink_bug_link": True,
            }
            compact = api.qa_preview_report_artifact(coordinator=coordinator, artifact_dir=tmpdir, **arguments)
            self.assertTrue(compact["ok"], compact)
            artifact = json.loads(Path(compact["result"]["preview_artifact"]).read_text(encoding="utf-8"))
            executed = api.qa_execute_preview_artifact(
                coordinator=coordinator,
                operation_id=arguments["operation_id"],
                preview_artifact=compact["result"]["preview_artifact"],
                preview_digest=compact["result"]["preview_digest"],
                write=True,
                audit_dir=tmpdir,
            )
            self.assertTrue(executed["ok"], executed)
            audit = json.loads(Path(executed["result"]["audit_file"]).read_text(encoding="utf-8"))

        self.assertTrue(compact["result"]["testlink_bug_link"])
        self.assertEqual("link-bug", artifact["review"]["items"][0]["testlink_mode"])
        self.assertEqual(1, len(ports.link_writes))
        self.assertEqual("link-bug", audit["items"][0]["testlink_mode"])
        self.assertEqual([], schema_errors(compact["result"], preview_schema, preview_schema))
        self.assertEqual([], schema_errors(artifact["review"], preview_schema, preview_schema))
        self.assertEqual([], schema_errors(audit, audit_schema, audit_schema))


if __name__ == "__main__":
    unittest.main()
