from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from qa_mcp_contracts import payload_digest
from qa_integration_agent import api
from qa_integration_agent.audit import read_workflow_audit
from qa_integration_agent.coordinator import QaCoordinator


class FakePorts:
    def __init__(self, *, redmine_action: str = "create") -> None:
        self.redmine_action = redmine_action
        self.created_issue = False
        self.testlink_write_failures = 0
        self.comment_write_failures = 0
        self.redmine_write_count = 0
        self.testlink_write_count = 0
        self.comment_write_count = 0
        self.testlink_notes: list[str] = []
        self.redmine_preview_requests: list[dict] = []

    def testlink_execution(self, **kwargs):
        plan = {
            "operation_id": kwargs["operation_id"],
            "environment": kwargs["environment"],
            "project": kwargs["project"],
            "plan": kwargs["plan"],
            "platform": kwargs["platform"],
            "build": kwargs["build"],
            "testcase_external_id": kwargs["testcase_external_id"],
            "status": kwargs["status"],
            "notes": kwargs["notes"],
        }
        digest = payload_digest(plan)
        target = {
            "project": {"id": "10", "name": kwargs["project"]},
            "plan": {"id": "20", "name": kwargs["plan"]},
            "platform": {"id": "30", "name": kwargs["platform"]},
            "build": {"id": "40", "name": kwargs["build"]},
        }
        if not kwargs.get("write"):
            return {
                "ok": True,
                "code": 0,
                "result": {
                    "schema_version": "1.0",
                    "operation_id": kwargs["operation_id"],
                    "environment": kwargs["environment"],
                    "mode": "preview",
                    "preview_digest": digest,
                    "planned_write": True,
                    "target": target,
                    "execution": {
                        "testcase_external_id": kwargs["testcase_external_id"],
                        "status": kwargs["status"],
                        "notes_digest": hashlib.sha256(kwargs["notes"].encode("utf-8")).hexdigest(),
                    },
                    "warnings": [],
                },
            }
        self.testlink_write_count += 1
        self.testlink_notes.append(kwargs["notes"])
        if self.testlink_write_failures:
            self.testlink_write_failures -= 1
            return {
                "ok": False,
                "code": 1,
                "error": {"error": {"code": "TL_WRITE", "message": "offline TestLink failure"}},
            }
        return {
            "ok": True,
            "code": 0,
            "result": {
                "schema_version": "1.0",
                "operation_id": kwargs["operation_id"],
                "environment": kwargs["environment"],
                "preview_digest": kwargs["preview_digest"],
                "status": "success",
                "testcase_external_id": kwargs["testcase_external_id"],
                "execution_id": "9001",
                "execution_url": None,
                "audit_id": "testlink-audit.json",
            },
        }

    def redmine_bug(self, **kwargs):
        if not kwargs.get("write"):
            self.redmine_preview_requests.append(dict(kwargs))
        effective_action = "reuse" if self.created_issue else self.redmine_action
        existing = {
            "id": "12345",
            "url": "https://redmine.example.com/issues/12345",
            "subject": "Existing issue",
            "state": "open",
        }
        plan = {
            "operation_id": kwargs["operation_id"],
            "environment": kwargs["environment"],
            "dedupe_marker": kwargs["dedupe_marker"],
            "action": effective_action,
            "subject": kwargs["subject"],
        }
        digest = payload_digest(plan)
        if not kwargs.get("write"):
            result = {
                "schema_version": "1.0",
                "operation_id": kwargs["operation_id"],
                "environment": kwargs["environment"],
                "mode": "preview",
                "preview_digest": digest,
                "planned_write": effective_action == "create",
                "action": effective_action,
                "dedupe_digest": hashlib.sha256(kwargs["dedupe_marker"].encode("utf-8")).hexdigest()[:16],
                "manager_fields_enabled": False,
                "blocked_fields": [],
                "warnings": [],
            }
            if effective_action == "reuse":
                result["existing_issue"] = existing
            return {"ok": True, "code": 0, "result": result}
        self.redmine_write_count += 1
        action = "reused" if effective_action == "reuse" else "created"
        if action == "created":
            self.created_issue = True
        return {
            "ok": True,
            "code": 0,
            "result": {
                "schema_version": "1.0",
                "operation_id": kwargs["operation_id"],
                "environment": kwargs["environment"],
                "preview_digest": kwargs["preview_digest"],
                "status": "success",
                "action": action,
                "issue": {
                    "id": "12345",
                    "url": "https://redmine.example.com/issues/12345",
                    "subject": "Issue",
                    "reused": action == "reused",
                },
                "comment_status": "not-required",
                "audit_id": "redmine-audit.json",
            },
        }

    def redmine_comment(self, **kwargs):
        plan = {
            "operation_id": kwargs["operation_id"],
            "environment": kwargs["environment"],
            "issue_id": kwargs["issue_id"],
            "notes": kwargs["notes"],
        }
        digest = payload_digest(plan)
        if not kwargs.get("write"):
            return {
                "ok": True,
                "code": 0,
                "result": {
                    "schema_version": "1.0",
                    "operation_id": kwargs["operation_id"],
                    "environment": kwargs["environment"],
                    "mode": "preview",
                    "preview_digest": digest,
                    "planned_write": True,
                    "issue_id": kwargs["issue_id"],
                    "notes_digest": hashlib.sha256(kwargs["notes"].encode("utf-8")).hexdigest(),
                    "warnings": [],
                },
            }
        self.comment_write_count += 1
        if self.comment_write_failures:
            self.comment_write_failures -= 1
            return {
                "ok": False,
                "code": 1,
                "error": {"error": {"code": "COMMENT_WRITE", "message": "offline comment failure"}},
            }
        return {
            "ok": True,
            "code": 0,
            "result": {
                "schema_version": "1.0",
                "operation_id": kwargs["operation_id"],
                "environment": kwargs["environment"],
                "preview_digest": kwargs["preview_digest"],
                "status": "added",
                "issue_id": kwargs["issue_id"],
                "audit_id": "comment-audit.json",
            },
        }


def write_report(directory: str, status: str = "Fail") -> Path:
    report = Path(directory) / "report.txt"
    report.write_text(
        "\n".join(
            [
                "Report generated on: 2026-07-13",
                "Test Results:",
                "-------------",
                f"[EMS-1][test_login] Result {status} (60s)",
            ]
        ),
        encoding="utf-8",
    )
    return report


def workflow_args(report: Path, **overrides):
    values = {
        "operation_id": "operation-workflow-1",
        "correlation_id": "correlation-workflow-1",
        "environment": "sandbox",
        "project": "EMS",
        "plan": "Regression",
        "platform": "Default Platform",
        "build": "build-1",
        "report": str(report),
        "redmine_create_bugs": True,
        "redmine_project_id": "ems",
        "redmine_tracker_id": "1",
        "redmine_priority_id": "2",
    }
    values.update(overrides)
    return values


class QaCoordinatorTests(unittest.TestCase):
    def test_preview_digest_is_stable_across_requested_at_changes(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            with patch("qa_integration_agent.coordinator.utc_now_iso", return_value="2026-07-13T01:00:00+00:00"):
                first = coordinator.build_plan(**workflow_args(report))
            with patch("qa_integration_agent.coordinator.utc_now_iso", return_value="2026-07-13T02:00:00+00:00"):
                second = coordinator.build_plan(**workflow_args(report))

        self.assertEqual(first["preview_digest"], second["preview_digest"])

    def test_coordinator_renders_legacy_template_tokens_before_redmine_preview(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            template = Path(tmpdir) / "redmine-template.json"
            template.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "priority_id": 2,
                        "required_custom_fields": [
                            {"id": 5, "name": "FW Ver"},
                            {"id": 31, "name": "Test case No"},
                        ],
                        "custom_fields": [
                            {"id": 5, "name": "FW Ver", "value": "{{context.build.name}}"},
                            {"id": 31, "name": "Test case No", "value": "{{result.external_id}}"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            coordinator.build_plan(**workflow_args(report, redmine_template_file=str(template)))

        fields = {str(item["id"]): item["value"] for item in ports.redmine_preview_requests[0]["custom_fields"]}
        self.assertEqual("build-1", fields["5"])
        self.assertEqual("EMS-1", fields["31"])
        self.assertNotIn("{{", json.dumps(fields))

    def test_aggregate_preview_has_zero_external_writes(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            plan = coordinator.build_plan(**workflow_args(report))
            preview = coordinator.public_preview(plan)

        self.assertEqual("preview", preview["mode"])
        self.assertEqual(1, preview["write_count"])
        self.assertEqual("create", preview["items"][0]["redmine_action"])
        self.assertEqual(0, ports.redmine_write_count)
        self.assertEqual(0, ports.testlink_write_count)
        self.assertEqual(0, ports.comment_write_count)

    def test_create_issue_then_testlink_write_produces_bidirectional_trace(self) -> None:
        ports = FakePorts(redmine_action="create")
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            plan = coordinator.build_plan(**workflow_args(report))
            result = coordinator.execute_plan(
                plan,
                confirmed_preview_digest=plan["preview_digest"],
                report=str(report),
                audit_dir=tmpdir,
            )
            audit = result["audit"]

        self.assertEqual("completed", result["status"])
        self.assertEqual(1, ports.redmine_write_count)
        self.assertEqual(1, ports.testlink_write_count)
        self.assertEqual(0, ports.comment_write_count)
        self.assertIn("REDMINE-ID: #12345", ports.testlink_notes[0])
        self.assertIn("Dedupe Key: testlink-agent:", ports.testlink_notes[0])
        self.assertEqual("created", audit["items"][0]["redmine_action"])
        self.assertTrue(coordinator.validate_traceability(audit)["valid"])

    def test_reused_issue_gets_evidence_comment_after_testlink_success(self) -> None:
        ports = FakePorts(redmine_action="reuse")
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            plan = coordinator.build_plan(**workflow_args(report))
            result = coordinator.execute_plan(
                plan,
                confirmed_preview_digest=plan["preview_digest"],
                report=str(report),
                audit_dir=tmpdir,
            )

        self.assertEqual("completed", result["status"])
        self.assertEqual(1, ports.comment_write_count)
        self.assertEqual("added", result["audit"]["items"][0]["evidence_comment"])
        self.assertTrue(coordinator.validate_traceability(result["audit"])["valid"])

    def test_changed_report_blocks_execute_before_external_writes(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            preview_plan = coordinator.build_plan(**workflow_args(report))
            write_report(tmpdir, status="Pass")
            changed_plan = coordinator.build_plan(**workflow_args(report))
            with self.assertRaisesRegex(Exception, "confirmed preview"):
                coordinator.execute_plan(
                    changed_plan,
                    confirmed_preview_digest=preview_plan["preview_digest"],
                    report=str(report),
                    audit_dir=tmpdir,
                )

        self.assertEqual(0, ports.redmine_write_count)
        self.assertEqual(0, ports.testlink_write_count)

    def test_redmine_success_testlink_failure_resumes_without_duplicate_issue(self) -> None:
        ports = FakePorts(redmine_action="create")
        ports.testlink_write_failures = 1
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            plan = coordinator.build_plan(**workflow_args(report))
            first = coordinator.execute_plan(
                plan,
                confirmed_preview_digest=plan["preview_digest"],
                report=str(report),
                audit_dir=tmpdir,
            )
            self.assertEqual("partial-failure", first["status"])
            resume_plan = coordinator.build_plan(**workflow_args(report))
            resumed = coordinator.execute_plan(
                resume_plan,
                confirmed_preview_digest=plan["preview_digest"],
                report=str(report),
                resume_audit=str(Path(tmpdir) / first["audit_id"]),
            )

        self.assertEqual("completed", resumed["status"])
        self.assertEqual(1, ports.redmine_write_count)
        self.assertEqual(2, ports.testlink_write_count)
        self.assertEqual("created", resumed["audit"]["items"][0]["redmine_action"])

    def test_comment_failure_resume_skips_testlink_and_retries_comment_only(self) -> None:
        ports = FakePorts(redmine_action="reuse")
        ports.comment_write_failures = 1
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            plan = coordinator.build_plan(**workflow_args(report))
            first = coordinator.execute_plan(
                plan,
                confirmed_preview_digest=plan["preview_digest"],
                report=str(report),
                audit_dir=tmpdir,
            )
            resume_plan = coordinator.build_plan(**workflow_args(report))
            resumed = coordinator.execute_plan(
                resume_plan,
                confirmed_preview_digest=plan["preview_digest"],
                report=str(report),
                resume_audit=str(Path(tmpdir) / first["audit_id"]),
            )

        self.assertEqual("completed", resumed["status"])
        self.assertEqual(1, ports.testlink_write_count)
        self.assertEqual(2, ports.comment_write_count)
        self.assertEqual("skipped-resume", resumed["audit"]["items"][0]["testlink_write"])
        self.assertEqual("added", resumed["audit"]["items"][0]["evidence_comment"])

    def test_public_api_requires_explicit_write_and_reads_audit(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            values = workflow_args(report)
            preview = api.qa_preview_report_import(coordinator=coordinator, **values)
            refused = api.qa_execute_report_import(
                coordinator=coordinator,
                preview_digest=preview["result"]["preview_digest"],
                write=False,
                audit_dir=tmpdir,
                **values,
            )
            executed = api.qa_execute_report_import(
                coordinator=coordinator,
                preview_digest=preview["result"]["preview_digest"],
                write=True,
                audit_dir=tmpdir,
                **values,
            )
            audit_path = Path(tmpdir) / executed["result"]["audit_id"]
            loaded = read_workflow_audit(audit_path)

        self.assertFalse(refused["ok"])
        self.assertTrue(executed["ok"])
        self.assertEqual("operation-workflow-1", loaded["operation_id"])


if __name__ == "__main__":
    unittest.main()
