from __future__ import annotations

import contextlib
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
    def test_build_plan_defaults_correlation_id_to_operation_id(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            args = workflow_args(report)
            args.pop("correlation_id")
            plan = coordinator.build_plan(**args)

        self.assertEqual(plan["operation_id"], plan["correlation_id"])

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

    def test_report_duration_becomes_testlink_execution_minutes(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            plan = coordinator.build_plan(**workflow_args(report, redmine_create_bugs=False))

        item = plan["items"][0]
        self.assertEqual(60.0, item["result"]["duration_seconds"])
        self.assertEqual(1.0, item["testlink_request"]["execution_duration"])


class SessionFakePorts(FakePorts):
    """FakePorts that records port sessions and which calls ran inside one."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sessions_opened = 0
        self.active = False
        self.calls_outside_session = 0
        self.testlink_calls = 0

    @contextlib.contextmanager
    def session(self):
        self.sessions_opened += 1
        self.active = True
        try:
            yield self
        finally:
            self.active = False

    def _track(self) -> None:
        if not self.active:
            self.calls_outside_session += 1

    def testlink_execution(self, **kwargs):
        self._track()
        self.testlink_calls += 1
        return super().testlink_execution(**kwargs)

    def redmine_bug(self, **kwargs):
        self._track()
        return super().redmine_bug(**kwargs)

    def redmine_comment(self, **kwargs):
        self._track()
        return super().redmine_comment(**kwargs)


def write_bulk_report(directory: str, count: int, *, fail_every: int = 0) -> Path:
    report = Path(directory) / "bulk-report.txt"
    rows = [
        f"[EMS-{index}][test_case_{index}] Result "
        f"{'Fail' if fail_every and index % fail_every == 0 else 'Pass'} ({index}s)"
        for index in range(1, count + 1)
    ]
    report.write_text(
        "\n".join(["Report generated on: 2026-07-13", "Test Results:", "-------------", *rows]),
        encoding="utf-8",
    )
    return report


class QaCoordinatorBatchSessionTests(unittest.TestCase):
    def test_preview_and_execute_each_run_in_one_port_session(self) -> None:
        cases = 40
        ports = SessionFakePorts(redmine_action="create")
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_bulk_report(tmpdir, cases, fail_every=10)
            plan = coordinator.build_plan(**workflow_args(report))
            self.assertEqual(1, ports.sessions_opened)
            self.assertEqual(cases, ports.testlink_calls)
            result = coordinator.execute_plan(
                plan,
                confirmed_preview_digest=plan["preview_digest"],
                report=str(report),
                audit_dir=tmpdir,
            )

        self.assertEqual(2, ports.sessions_opened)
        self.assertEqual(0, ports.calls_outside_session)
        self.assertEqual("completed", result["status"])
        self.assertEqual(cases, ports.testlink_write_count)
        # Per item: one preview at plan time, one final preview and one write at execute time.
        self.assertEqual(3 * cases, ports.testlink_calls)
        self.assertEqual(
            [f"EMS-{index}" for index in range(1, cases + 1)],
            [item["testcase_external_id"] for item in result["audit"]["items"]],
        )
        self.assertEqual(
            len({item["testlink_request"]["operation_id"] for item in plan["items"]}),
            cases,
        )

    def test_partial_failure_in_a_session_batch_resumes_only_missing_items(self) -> None:
        ports = SessionFakePorts(redmine_action="create")
        ports.testlink_write_failures = 1
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_bulk_report(tmpdir, 5)
            values = workflow_args(report, redmine_create_bugs=False)
            preview = api.qa_preview_report_artifact(coordinator=coordinator, artifact_dir=tmpdir, **values)
            first = api.qa_execute_preview_artifact(
                coordinator=coordinator,
                operation_id=values["operation_id"],
                preview_artifact=preview["result"]["preview_artifact"],
                preview_digest=preview["result"]["preview_digest"],
                write=True,
                audit_dir=tmpdir,
            )
            audit_after_failure = read_workflow_audit(first["result"]["audit_file"])
            resumed = api.qa_resume_preview_artifact(
                coordinator=coordinator,
                operation_id=values["operation_id"],
                preview_artifact=preview["result"]["preview_artifact"],
                audit_file=first["result"]["audit_file"],
                write=True,
            )
            audit_after_resume = read_workflow_audit(first["result"]["audit_file"])

        self.assertEqual("partial-failure", first["result"]["status"])
        self.assertEqual(
            ["failed", "success", "success", "success", "success"],
            [item["testlink_write"] for item in audit_after_failure["items"]],
        )
        self.assertEqual("completed", resumed["result"]["status"])
        self.assertEqual(
            ["success", "skipped-resume", "skipped-resume", "skipped-resume", "skipped-resume"],
            [item["testlink_write"] for item in audit_after_resume["items"]],
        )
        # Five first-run writes (one failed) plus exactly one retried write.
        self.assertEqual(6, ports.testlink_write_count)
        self.assertEqual(3, ports.sessions_opened)
        self.assertEqual(0, ports.calls_outside_session)


def write_node_report(directory: str, name: str, node: str, rows: list[str], *, ip: str = "192.0.2.1") -> Path:
    report = Path(directory) / name
    report.write_text(
        "\n".join(
            [
                "Report generated on: 2026-07-13 10:00:00",
                "EMS Version: 1.2.3",
                f"Node Name: {node}",
                f"Node IP: {ip}",
                "Node Chassis: MSC8000",
                "Test Results:",
                "-------------",
                *rows,
            ]
        ),
        encoding="utf-8",
    )
    return report


def multi_args(reports: list[tuple[str, Path]], **overrides):
    values = workflow_args(Path("unused"), **{"redmine_create_bugs": False, **overrides})
    values.pop("report")
    values["reports"] = [{"label": label, "path": str(path)} for label, path in reports]
    return values


class MultiReportImportTests(unittest.TestCase):
    def build(self, rows_by_node: dict[str, list[str]], ports=None, **overrides):
        ports = ports or FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            reports = [
                (label, write_node_report(tmpdir, f"{label}.txt", f"OLT-{label}", rows, ip=f"192.0.2.{index + 1}"))
                for index, (label, rows) in enumerate(rows_by_node.items())
            ]
            plan = coordinator.build_plan(**multi_args(reports, **overrides))
        return plan, ports

    def test_status_aggregation_fail_wins_then_pass_then_blocked(self) -> None:
        plan, _ = self.build(
            {
                "node-a": [
                    "[EMS-1][test_one] Result Pass (10s)",
                    "[EMS-2][test_two] Result Pass (10s)",
                    "[EMS-3][test_three] Result Skip (1s)",
                    "[EMS-4][test_four] Result Pass (10s)",
                ],
                "node-b": [
                    "[EMS-1][test_one] Result Error (20s)",
                    "[EMS-2][test_two] Result Pass (30s)",
                    "[EMS-3][test_three] Result Blocked (2s)",
                    "[EMS-4][test_four] Result Blocked (10s)",
                ],
                "node-c": [
                    "[EMS-1][test_one] Result Fail (15s)",
                    "[EMS-2][test_two] Result Skip (1s)",
                    "[EMS-3][test_three] Result Skip (1s)",
                    "[EMS-4][test_four] Result Skip (1s)",
                ],
            }
        )

        results = {item["result"]["external_id"]: item["result"] for item in plan["items"]}
        self.assertEqual("2.0", plan["schema_version"])
        self.assertEqual({"EMS-1": "f", "EMS-2": "p", "EMS-3": "b", "EMS-4": "p"},
                         {key: value["status"] for key, value in results.items()})
        # The first failing node in reports order is the lead: node-b's Error.
        self.assertEqual("Error", results["EMS-1"]["raw_status"])
        self.assertEqual("Blocked", results["EMS-3"]["raw_status"])
        self.assertEqual(["node-a", "node-b", "node-c"], [node["label"] for node in results["EMS-1"]["nodes"]])
        self.assertEqual([], plan["ignored"])

    def test_all_nodes_skipped_follow_skip_policy(self) -> None:
        rows = {
            "node-a": ["[EMS-1][test_one] Result Skip (1s)", "[EMS-2][test_two] Result Pass (1s)"],
            "node-b": ["[EMS-1][test_one] Result Skipped (1s)", "[EMS-2][test_two] Result Pass (1s)"],
        }
        ignored_plan, _ = self.build(rows)
        blocked_plan, _ = self.build(rows, skip_policy="blocked")

        self.assertEqual(["EMS-2"], [item["result"]["external_id"] for item in ignored_plan["items"]])
        self.assertEqual(["EMS-1"], [row["external_id"] for row in ignored_plan["ignored"]])
        self.assertEqual(2, len(ignored_plan["ignored"][0]["nodes"]))
        blocked = {item["result"]["external_id"]: item["result"] for item in blocked_plan["items"]}
        self.assertEqual("b", blocked["EMS-1"]["status"])
        self.assertEqual("Skip", blocked["EMS-1"]["raw_status"])
        self.assertEqual([], blocked_plan["ignored"])

    def test_notes_list_every_node_and_duration_is_the_longest(self) -> None:
        plan, ports = self.build(
            {
                "node-a": ["[EMS-1][test_one] Result Pass (60s)"],
                "node-b": ["[EMS-1][test_one] Result Fail (90s)"],
            }
        )

        request = plan["items"][0]["testlink_request"]
        notes = request["notes"].splitlines()
        self.assertEqual(1.5, request["execution_duration"])
        self.assertEqual(90.0, plan["items"][0]["result"]["duration_seconds"])
        self.assertIn("Report File: node-a.txt, node-b.txt", notes)
        self.assertIn("Result: Fail", notes)
        start = notes.index("Node Results:")
        self.assertEqual(
            [
                "node-a: Result Pass; duration 60s; target OLT-node-a / 192.0.2.1 / MSC8000; report node-a.txt",
                "node-b: Result Fail; duration 90s; target OLT-node-b / 192.0.2.2 / MSC8000; report node-b.txt",
            ],
            notes[start + 1:start + 3],
        )
        self.assertEqual(0, ports.testlink_write_count)

    def test_plan_records_every_report_and_digests_their_hashes(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            first = write_node_report(tmpdir, "a.txt", "OLT-A", ["[EMS-1][test_one] Result Pass (1s)"])
            second = write_node_report(tmpdir, "b.txt", "OLT-B", ["[EMS-1][test_one] Result Pass (1s)"])
            plan = coordinator.build_plan(**multi_args([("node-a", first), ("node-b", second)]))
            hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)]
            swapped = coordinator.build_plan(**multi_args([("node-b", second), ("node-a", first)]))

        self.assertEqual(
            [
                {
                    "label": "node-a",
                    "path": str(first),
                    "report_file": "a.txt",
                    "sha256": hashes[0],
                    "report_schema": "legacy-web-ems-report-v1",
                    "header": {
                        "Report generated on": "2026-07-13 10:00:00",
                        "EMS Version": "1.2.3",
                        "Node Name": "OLT-A",
                        "Node IP": "192.0.2.1",
                        "Node Chassis": "MSC8000",
                    },
                },
                "node-b",
            ],
            [plan["reports"][0], plan["reports"][1]["label"]],
        )
        self.assertEqual(
            payload_digest({"reports": [{"label": "node-a", "sha256": hashes[0]},
                                        {"label": "node-b", "sha256": hashes[1]}]}),
            plan["input_digest"],
        )
        self.assertNotIn("report", plan)
        self.assertNotEqual(plan["input_digest"], swapped["input_digest"])

    def test_testcase_missing_from_one_report_fails_before_any_preview(self) -> None:
        ports = FakePorts()
        with self.assertRaises(Exception) as context:
            self.build(
                {
                    "node-a": ["[EMS-1][test_one] Result Pass (1s)", "[EMS-2][test_two] Result Pass (1s)"],
                    "node-b": ["[EMS-1][test_one] Result Pass (1s)"],
                },
                ports=ports,
            )

        self.assertEqual("INVALID_ARGUMENT", context.exception.code)
        self.assertIn("node-b lacks EMS-2", str(context.exception))
        self.assertEqual([], ports.redmine_preview_requests)

    def test_duplicate_testcase_within_one_report_is_rejected(self) -> None:
        with self.assertRaises(Exception) as context:
            self.build(
                {
                    "node-a": ["[EMS-1][test_one] Result Pass (1s)", "[EMS-1][test_one] Result Skip (1s)"],
                    "node-b": ["[EMS-1][test_one] Result Pass (1s)"],
                }
            )

        self.assertEqual("DUPLICATE_CASE", context.exception.code)
        self.assertIn("node-a", str(context.exception))

    def test_report_labels_and_entries_are_validated(self) -> None:
        coordinator = QaCoordinator(FakePorts())
        with TemporaryDirectory() as tmpdir:
            first = str(write_node_report(tmpdir, "a.txt", "A", ["[EMS-1][t] Result Pass (1s)"]))
            second = str(write_node_report(tmpdir, "b.txt", "B", ["[EMS-1][t] Result Pass (1s)"]))
            invalid = {
                "empty list": [],
                "blank label": [{"label": "  ", "path": first}],
                "non-string label": [{"label": 7, "path": first}],
                "duplicate label": [{"label": "Node", "path": first}, {"label": "node", "path": second}],
                "control character": [{"label": "node\nB", "path": first}],
                "long label": [{"label": "n" * 65, "path": first}],
                "unknown key": [{"label": "a", "path": first, "status": "Pass"}],
                "repeated file": [{"label": "a", "path": first}, {"label": "b", "path": first}],
                "not a list": {"label": "a", "path": first},
            }
            codes = {}
            for name, reports in invalid.items():
                arguments = multi_args([])
                arguments["reports"] = reports
                with self.assertRaises(Exception) as context:
                    coordinator.build_plan(**arguments)
                codes[name] = context.exception.code
            arguments = multi_args([])
            arguments["reports"] = [{"label": "a", "path": str(Path(tmpdir) / "missing.txt")}]
            with self.assertRaises(Exception) as missing:
                coordinator.build_plan(**arguments)

        self.assertEqual({"INVALID_ARGUMENT"}, set(codes.values()), codes)
        self.assertEqual("REPORT_NOT_FOUND", missing.exception.code)

    def test_report_and_reports_are_mutually_exclusive(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            both = workflow_args(report)
            both["reports"] = [{"label": "a", "path": str(report)}]
            neither = workflow_args(report)
            neither.pop("report")
            refused = [
                api.qa_preview_report_artifact(coordinator=coordinator, artifact_dir=tmpdir, **arguments)
                for arguments in (both, neither)
            ]
            legacy = api.qa_preview_report_import(coordinator=coordinator, **{**neither, "reports": both["reports"]})

        for response in refused:
            self.assertFalse(response["ok"])
            self.assertEqual("INVALID_ARGUMENT", response["error"]["error"]["code"])
        # The legacy compatibility preview keeps the single-report contract.
        self.assertFalse(legacy["ok"])
        self.assertEqual("INVALID_ARGUMENT", legacy["error"]["error"]["code"])
        self.assertEqual([], ports.redmine_preview_requests)

    def test_single_report_plan_keeps_the_v1_shape(self) -> None:
        coordinator = QaCoordinator(FakePorts())
        with TemporaryDirectory() as tmpdir:
            report = write_report(tmpdir)
            plan = coordinator.build_plan(**workflow_args(report))
            preview = coordinator.public_preview(plan)
            report_hash = hashlib.sha256(report.read_bytes()).hexdigest()

        self.assertEqual("1.0", plan["schema_version"])
        self.assertEqual(report_hash, plan["input_digest"])
        self.assertEqual(str(report), plan["report"])
        self.assertNotIn("reports", plan)
        self.assertNotIn("nodes", plan["items"][0]["result"])
        self.assertNotIn("Node Results:", plan["items"][0]["testlink_request"]["notes"])
        self.assertNotIn("Failing Nodes:", plan["items"][0]["redmine_request"]["description"])
        self.assertEqual("legacy-web-ems-report-v1", preview["report_schema"])
        self.assertNotIn("reports", preview)

    def test_redmine_uses_the_first_failing_node_and_lists_all_nodes(self) -> None:
        ports = FakePorts(redmine_action="create")
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            template = Path(tmpdir) / "redmine-template.json"
            template.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "priority_id": 2,
                        "required_custom_fields": [{"id": 40, "name": "Node"}, {"id": 41, "name": "Result"}],
                        "custom_fields": [
                            {"id": 40, "name": "Node", "value": "{{header.Node Name}}"},
                            {"id": 41, "name": "Result", "value": "{{result.raw_status}}"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            reports = [
                ("node-a", write_node_report(tmpdir, "a.txt", "OLT-A", ["[EMS-1][test_one] Result Pass (1s)"])),
                ("node-b", write_node_report(tmpdir, "b.txt", "OLT-B", ["[EMS-1][test_one] Result Error (2s)"])),
                ("node-c", write_node_report(tmpdir, "c.txt", "OLT-C", ["[EMS-1][test_one] Result Fail (3s)"])),
            ]
            plan = coordinator.build_plan(
                **multi_args(
                    reports,
                    redmine_create_bugs=True,
                    redmine_template_file=str(template),
                )
            )
            single_node_b = coordinator.build_plan(
                **workflow_args(reports[1][1], redmine_create_bugs=True, redmine_template_file=str(template))
            )

        request = ports.redmine_preview_requests[0]
        fields = {str(field["id"]): field["value"] for field in request["custom_fields"]}
        self.assertEqual({"40": "OLT-B", "41": "Error"}, fields)
        self.assertEqual("[EMS-1] test_one Result Error", request["subject"])
        description = request["description"].splitlines()
        self.assertIn("Failing Nodes: node-b, node-c", description)
        self.assertEqual(3, sum(1 for line in description if line.startswith("node-")))
        # Dedupe follows the lead failing node, so it matches importing that node's report alone.
        self.assertEqual(single_node_b["items"][0]["dedupe_digest"], plan["items"][0]["dedupe_digest"])

    def test_reused_issue_evidence_comment_lists_every_node(self) -> None:
        ports = FakePorts(redmine_action="reuse")
        comments: list[str] = []
        original_comment = ports.redmine_comment

        def record_comment(**kwargs):
            if kwargs.get("write"):
                comments.append(kwargs["notes"])
            return original_comment(**kwargs)

        ports.redmine_comment = record_comment
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            reports = [
                ("node-a", write_node_report(tmpdir, "a.txt", "OLT-A", ["[EMS-1][test_one] Result Fail (4s)"])),
                ("node-b", write_node_report(tmpdir, "b.txt", "OLT-B", ["[EMS-1][test_one] Result Pass (2s)"])),
            ]
            plan = coordinator.build_plan(**multi_args(reports, redmine_create_bugs=True))
            result = coordinator.execute_plan(
                plan, confirmed_preview_digest=plan["preview_digest"], audit_dir=tmpdir,
            )

        self.assertEqual("completed", result["status"])
        lines = comments[0].splitlines()
        self.assertIn("Report File: a.txt, b.txt", lines)
        self.assertIn("node-a: Result Fail; duration 4s; target OLT-A / 192.0.2.1 / MSC8000; report a.txt", lines)
        self.assertIn("node-b: Result Pass; duration 2s; target OLT-B / 192.0.2.1 / MSC8000; report b.txt", lines)
        self.assertIn("REDMINE-ID: #12345", ports.testlink_notes[0])

    def test_execute_and_resume_refuse_any_changed_report(self) -> None:
        ports = FakePorts()
        ports.testlink_write_failures = 1
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            first = write_node_report(tmpdir, "a.txt", "OLT-A", ["[EMS-1][t1] Result Pass (1s)",
                                                                  "[EMS-2][t2] Result Pass (1s)"])
            second = write_node_report(tmpdir, "b.txt", "OLT-B", ["[EMS-1][t1] Result Pass (1s)",
                                                                   "[EMS-2][t2] Result Pass (1s)"])
            values = multi_args([("node-a", first), ("node-b", second)])
            preview = api.qa_preview_report_artifact(coordinator=coordinator, artifact_dir=tmpdir, **values)
            execute = {
                "coordinator": coordinator,
                "operation_id": values["operation_id"],
                "preview_artifact": preview["result"]["preview_artifact"],
                "preview_digest": preview["result"]["preview_digest"],
                "write": True,
                "audit_dir": tmpdir,
            }
            original = second.read_bytes()
            second.write_bytes(original + b"\n")
            refused_execute = api.qa_execute_preview_artifact(**execute)
            second.write_bytes(original)
            first_run = api.qa_execute_preview_artifact(**execute)
            writes_after_first_run = ports.testlink_write_count
            second.write_bytes(original.replace(b"Pass (1s)", b"Fail (1s)", 1))
            resume = {
                "coordinator": coordinator,
                "operation_id": values["operation_id"],
                "preview_artifact": preview["result"]["preview_artifact"],
                "audit_file": first_run["result"]["audit_file"],
                "write": True,
            }
            refused_resume = api.qa_resume_preview_artifact(**resume)
            writes_after_refused_resume = ports.testlink_write_count
            second.write_bytes(original)
            resumed = api.qa_resume_preview_artifact(**resume)

        self.assertFalse(refused_execute["ok"])
        self.assertEqual("PREVIEW_MISMATCH", refused_execute["error"]["error"]["code"])
        self.assertIn("node-b", refused_execute["error"]["error"]["message"])
        self.assertEqual("partial-failure", first_run["result"]["status"])
        self.assertEqual(2, writes_after_first_run)
        self.assertFalse(refused_resume["ok"])
        self.assertEqual("RESUME_MISMATCH", refused_resume["error"]["error"]["code"])
        self.assertEqual(writes_after_first_run, writes_after_refused_resume)
        self.assertEqual("completed", resumed["result"]["status"])
        self.assertEqual(3, ports.testlink_write_count)

    def test_resume_rejects_an_audit_bound_to_other_report_hashes(self) -> None:
        ports = FakePorts()
        ports.testlink_write_failures = 1
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            first = write_node_report(tmpdir, "a.txt", "OLT-A", ["[EMS-1][t1] Result Pass (1s)"])
            second = write_node_report(tmpdir, "b.txt", "OLT-B", ["[EMS-1][t1] Result Pass (1s)"])
            plan = coordinator.build_plan(**multi_args([("node-a", first), ("node-b", second)]))
            first_run = coordinator.execute_plan(
                plan, confirmed_preview_digest=plan["preview_digest"], audit_dir=tmpdir,
            )
            audit_path = Path(tmpdir) / first_run["audit_id"]
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["workflow"]["reports"][1]["sha256"] = "0" * 64
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            with self.assertRaises(Exception) as context:
                coordinator.execute_plan(
                    plan, confirmed_preview_digest=plan["preview_digest"], resume_audit=str(audit_path),
                )

        self.assertEqual("RESUME_MISMATCH", context.exception.code)
        self.assertEqual(1, ports.testlink_write_count)


if __name__ == "__main__":
    unittest.main()
