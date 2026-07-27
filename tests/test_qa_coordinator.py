from __future__ import annotations

import hashlib
import json
import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from qa_mcp_contracts import payload_digest
from qa_integration_agent import api
from qa_integration_agent.audit import read_workflow_audit
from qa_integration_agent.coordinator import QaCoordinator


class FakePorts:
    def __init__(
        self,
        *,
        redmine_action: str = "create",
        include_field_verification: bool = True,
        verification_failure_response: bool = False,
    ) -> None:
        self.redmine_action = redmine_action
        self.include_field_verification = include_field_verification
        self.verification_failure_response = verification_failure_response
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
        severity_label = kwargs.get("severity")
        priority_id = kwargs.get("priority_id") or {"L3": "4", "L2": "5", "L1": "6"}.get(
            severity_label
        )
        custom_priority = kwargs.get("custom_priority")
        issue_fields = {
            "severity": {
                "label": severity_label,
                "transport_field": "priority_id",
                "priority_id": priority_id,
                "display": f"{severity_label} (Redmine priority_id={priority_id})" if severity_label else None,
            },
            "priority": {
                "value": custom_priority or None,
                "display": (
                    f"{custom_priority} (custom field ID 119)"
                    if custom_priority
                    else "blank (custom field ID 119)"
                ),
                "transport_field": "custom_fields",
                "custom_field_id": "119",
            } if severity_label else None,
        }
        issue_payload = {
            "project_id": kwargs.get("project_id"),
            "subject": kwargs["subject"],
            "description": kwargs["description"],
            "tracker_id": kwargs.get("tracker_id"),
            "priority_id": priority_id,
        }
        if custom_priority:
            issue_payload["custom_fields"] = [{"id": "119", "value": custom_priority}]
        plan = {
            "operation_id": kwargs["operation_id"],
            "environment": kwargs["environment"],
            "dedupe_marker": kwargs["dedupe_marker"],
            "action": effective_action,
            "subject": kwargs["subject"],
            "issue_fields": issue_fields,
            "issue_payload": issue_payload,
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
                "issue_fields": issue_fields,
                "issue_payload": issue_payload,
            }
            if effective_action == "reuse":
                result["existing_issue"] = existing
            return {"ok": True, "code": 0, "result": result}
        self.redmine_write_count += 1
        action = "reused" if effective_action == "reuse" else "created"
        if action == "created":
            self.created_issue = True
        if action == "created" and self.verification_failure_response:
            return {
                "ok": False,
                "code": 1,
                "error": {
                    "error": {
                        "code": "VERIFICATION_FAILED",
                        "message": "field mismatch",
                    },
                    "partial_result": {
                        "action": "created",
                        "issue": {
                            "id": "12345",
                            "url": "https://redmine.example.com/issues/12345",
                            "subject": "Issue",
                            "reused": False,
                        },
                        "field_verification": {
                            "status": "verification_failed",
                            "verified": False,
                        },
                        "audit_id": "redmine-audit.json",
                    },
                },
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
                "action": action,
                "issue": {
                    "id": "12345",
                    "url": "https://redmine.example.com/issues/12345",
                    "subject": "Issue",
                    "reused": action == "reused",
                },
                "field_verification": {
                    "status": "not-required-reused" if action == "reused" else "verified",
                    "verified": None if action == "reused" else True,
                    "severity": None if action == "reused" else {"match": True},
                    "priority": None,
                } if self.include_field_verification else None,
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

    def test_aggregate_preview_exposes_distinct_severity_and_custom_priority(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            plan = coordinator.build_plan(
                **workflow_args(
                    report,
                    redmine_priority_id=None,
                    redmine_severity="L2",
                    redmine_custom_priority=None,
                )
            )
            preview = coordinator.public_preview(plan)

        item = preview["items"][0]
        self.assertEqual("L2", item["redmine_issue_fields"]["severity"]["label"])
        self.assertEqual("5", item["redmine_issue_fields"]["severity"]["priority_id"])
        self.assertEqual("blank (custom field ID 119)", item["redmine_issue_fields"]["priority"]["display"])
        self.assertEqual("5", item["redmine_issue_payload"]["priority_id"])

    def test_compact_preview_size_is_bounded_for_bulk_items(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            plan = coordinator.build_plan(**workflow_args(report))
            first = coordinator.public_preview(plan, include_items=False)
            template = plan["items"][0]
            plan["items"] = []
            for index in range(100):
                item = copy.deepcopy(template)
                external_id = f"EMS-{index + 1}"
                item["result"]["external_id"] = external_id
                plan["items"].append(item)
            bulk = coordinator.public_preview(plan, include_items=False)

        self.assertNotIn("items", bulk)
        self.assertEqual(10, len(bulk["summary"]["sample_testcase_external_ids"]))
        self.assertLess(len(json.dumps(bulk)), len(json.dumps(first)) * 2)

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
        self.assertTrue(audit["items"][0]["redmine_field_verification"]["verified"])
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

    def test_created_issue_without_readback_verification_stops_before_testlink_write(self) -> None:
        ports = FakePorts(redmine_action="create", include_field_verification=False)
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

        self.assertEqual("partial-failure", result["status"])
        self.assertEqual("12345", result["audit"]["items"][0]["redmine_issue_id"])
        self.assertEqual(0, ports.testlink_write_count)

    def test_verification_failure_partial_result_is_audited_and_resume_stays_blocked(self) -> None:
        ports = FakePorts(redmine_action="create", verification_failure_response=True)
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

        item = resumed["audit"]["items"][0]
        self.assertEqual("partial-failure", first["status"])
        self.assertEqual("partial-failure", resumed["status"])
        self.assertEqual("12345", item["redmine_issue_id"])
        self.assertFalse(item["redmine_field_verification"]["verified"])
        self.assertEqual(1, ports.redmine_write_count)
        self.assertEqual(0, ports.testlink_write_count)

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
            preview = api.qa_preview_report_artifact(
                coordinator=coordinator,
                artifact_dir=tmpdir,
                **values,
            )
            refused = api.qa_execute_preview_artifact(
                coordinator=coordinator,
                operation_id=values["operation_id"],
                preview_artifact=preview["result"]["preview_artifact"],
                preview_digest=preview["result"]["preview_digest"],
                write=False,
                audit_dir=tmpdir,
            )
            executed = api.qa_execute_preview_artifact(
                coordinator=coordinator,
                operation_id=values["operation_id"],
                preview_artifact=preview["result"]["preview_artifact"],
                preview_digest=preview["result"]["preview_digest"],
                write=True,
                audit_dir=tmpdir,
            )
            audit_path = Path(tmpdir) / executed["result"]["audit_id"]
            loaded = read_workflow_audit(audit_path)
            compact_operation = api.qa_get_operation(
                operation_id=values["operation_id"],
                audit_file=str(audit_path),
            )

        self.assertFalse(refused["ok"])
        self.assertTrue(executed["ok"])
        self.assertNotIn("items", preview["result"])
        self.assertEqual("review", preview["result"]["review_path"])
        self.assertNotIn("audit", executed["result"])
        self.assertNotIn("items", compact_operation["result"])
        self.assertEqual(1, compact_operation["result"]["item_count"])
        self.assertEqual("operation-workflow-1", loaded["operation_id"])

    def test_artifact_execute_rejects_changed_report_before_external_writes(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            values = workflow_args(report)
            preview = api.qa_preview_report_artifact(
                coordinator=coordinator,
                artifact_dir=tmpdir,
                **values,
            )
            write_report(tmpdir, status="Pass")
            executed = api.qa_execute_preview_artifact(
                coordinator=coordinator,
                operation_id=values["operation_id"],
                preview_artifact=preview["result"]["preview_artifact"],
                preview_digest=preview["result"]["preview_digest"],
                write=True,
                audit_dir=tmpdir,
            )

        self.assertFalse(executed["ok"])
        self.assertEqual("PREVIEW_MISMATCH", executed["error"]["error"]["code"])
        self.assertEqual(0, ports.redmine_write_count)
        self.assertEqual(0, ports.testlink_write_count)

    def test_artifact_execute_rejects_tampered_plan_before_external_writes(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            values = workflow_args(report)
            preview = api.qa_preview_report_artifact(
                coordinator=coordinator,
                artifact_dir=tmpdir,
                **values,
            )
            artifact_path = Path(preview["result"]["preview_artifact"])
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            artifact["plan"]["target"]["build"] = "tampered-build"
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            executed = api.qa_execute_preview_artifact(
                coordinator=coordinator,
                operation_id=values["operation_id"],
                preview_artifact=str(artifact_path),
                preview_digest=preview["result"]["preview_digest"],
                write=True,
                audit_dir=tmpdir,
            )

        self.assertFalse(executed["ok"])
        self.assertEqual("PREVIEW_MISMATCH", executed["error"]["error"]["code"])
        self.assertEqual(0, ports.redmine_write_count)
        self.assertEqual(0, ports.testlink_write_count)

    def test_public_resume_uses_same_artifact_and_audit_identity(self) -> None:
        ports = FakePorts(redmine_action="create")
        ports.testlink_write_failures = 1
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            values = workflow_args(report)
            preview = api.qa_preview_report_artifact(
                coordinator=coordinator,
                artifact_dir=tmpdir,
                **values,
            )
            first = api.qa_execute_preview_artifact(
                coordinator=coordinator,
                operation_id=values["operation_id"],
                preview_artifact=preview["result"]["preview_artifact"],
                preview_digest=preview["result"]["preview_digest"],
                write=True,
                audit_dir=tmpdir,
            )
            resumed = api.qa_resume_preview_artifact(
                coordinator=coordinator,
                operation_id=values["operation_id"],
                preview_artifact=preview["result"]["preview_artifact"],
                audit_file=first["result"]["audit_file"],
                write=True,
            )

        self.assertEqual("partial-failure", first["result"]["status"])
        self.assertEqual("completed", resumed["result"]["status"])
        self.assertEqual(1, ports.redmine_write_count)
        self.assertEqual(2, ports.testlink_write_count)

    def test_legacy_execute_contract_remains_available_in_all_toolset(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            values = workflow_args(report)
            preview = api.qa_preview_report_import(
                coordinator=coordinator,
                artifact_dir=tmpdir,
                **values,
            )
            executed = api.qa_execute_report_import(
                coordinator=coordinator,
                preview_digest=preview["result"]["preview_digest"],
                write=True,
                audit_dir=tmpdir,
                **values,
            )

        self.assertTrue(executed["ok"])
        self.assertIn("items", preview["result"])
        self.assertIn("audit", executed["result"])


if __name__ == "__main__":
    unittest.main()
