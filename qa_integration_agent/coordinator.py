from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from qa_mcp_contracts import CONTRACT_SCHEMA_VERSION, payload_digest, validate_operation_context
from testlink_agent_core.policy import build_dedupe_key, dedupe_digest, dedupe_marker
from testlink_agent_core.reports import SCHEMA_HEADER_KEY, parse_report, result_to_dict

from .audit import DEFAULT_AUDIT_DIR, read_workflow_audit, utc_now_iso, write_workflow_audit
from .errors import CoordinatorError, normalize_error
from .ports import IntegrationPorts, StdioMcpPorts
from .template import render_custom_fields


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def child_operation_id(operation_id: str, external_id: str, stage: str) -> str:
    suffix = hashlib.sha256(f"{external_id}:{stage}".encode("utf-8")).hexdigest()[:16]
    return f"{operation_id[:96]}-{suffix}"[:128]


def require_result(response: dict[str, Any], stage: str) -> dict[str, Any]:
    if not response.get("ok"):
        error = response.get("error") or {}
        detail = error.get("error") if isinstance(error, dict) else error
        if isinstance(detail, dict):
            message = str(detail.get("message") or detail)
            code = str(detail.get("code") or "PORT_ERROR")
        else:
            message = str(detail)
            code = "PORT_ERROR"
        raise CoordinatorError(f"{stage}: {message}", code=code)
    result = response.get("result")
    if not isinstance(result, dict):
        raise CoordinatorError(f"{stage} returned no structured result.", code="PORT_INVALID_RESPONSE")
    return result


class QaCoordinator:
    def __init__(self, ports: IntegrationPorts | None = None):
        self.ports = ports or StdioMcpPorts()

    @staticmethod
    def _base_notes(
        *,
        operation_id: str,
        report_path: Path,
        report_schema: str,
        project: str,
        plan: str,
        platform: str,
        build: str,
        result: dict[str, Any],
        dedupe_marker_value: str | None,
    ) -> str:
        lines = [
            f"Operation ID: {operation_id}",
            f"Report Schema: {report_schema}",
            f"Report File: {report_path.name}",
            f"TestLink Project: {project}",
            f"Test Plan: {plan}",
            f"Platform: {platform}",
            f"Build: {build}",
            f"Test Case: {result['external_id']}",
            f"Automation Test Function: {result['test_name']}",
            f"Result: {result['raw_status']}",
        ]
        if dedupe_marker_value:
            lines.append(f"Dedupe Key: {dedupe_marker_value}")
        return "\n".join(lines)

    @staticmethod
    def _redmine_description(
        *,
        operation_id: str,
        report_path: Path,
        project: str,
        plan: str,
        platform: str,
        build: str,
        result: dict[str, Any],
        marker: str,
    ) -> str:
        return "\n".join(
            [
                "Automation failure coordinated by qa-integration-agent.",
                "",
                f"Operation ID: {operation_id}",
                f"TestLink Project: {project}",
                f"Test Plan: {plan}",
                f"Platform: {platform}",
                f"Build: {build}",
                f"Test Case: {result['external_id']}",
                f"Test Case Name: {result.get('testlink_name') or ''}",
                f"Automation Test Function: {result['test_name']}",
                f"Result: {result['raw_status']}",
                f"Report File: {report_path.name}",
                "Execution URL:",
                f"Dedupe Key: {marker}",
            ]
        )

    @staticmethod
    def _final_notes(base_notes: str, issue: dict[str, Any] | None) -> str:
        if not issue:
            return base_notes
        return "\n".join(
            [
                base_notes,
                f"REDMINE-ID: #{issue['id']}",
                f"REDMINE-URL: {issue['url']}",
                f"REDMINE-REUSED: {'yes' if issue.get('reused') else 'no'}",
            ]
        )

    @staticmethod
    def _evidence_comment(
        *,
        operation_id: str,
        project: str,
        plan: str,
        platform: str,
        build: str,
        result: dict[str, Any],
        report_path: Path,
        marker: str,
        execution_id: str | None,
    ) -> str:
        return "\n".join(
            [
                "Automation retest evidence from qa-integration-agent.",
                f"Operation ID: {operation_id}",
                f"TestLink Project: {project}",
                f"Test Plan: {plan}",
                f"Platform: {platform}",
                f"Build: {build}",
                f"Test Case: {result['external_id']}",
                f"Automation Test Function: {result['test_name']}",
                f"Result: {result['raw_status']}",
                f"Report File: {report_path.name}",
                f"TestLink Execution ID: {execution_id or ''}",
                f"Dedupe Key: {marker}",
                "This comment does not change Redmine status, assignee, or fixed version.",
            ]
        )

    def build_plan(
        self,
        *,
        operation_id: str,
        correlation_id: str | None,
        environment: str,
        project: str,
        plan: str,
        platform: str,
        build: str,
        report: str,
        skip_policy: str = "ignore",
        redmine_create_bugs: bool = False,
        redmine_project_id: str | None = None,
        redmine_tracker_id: str | None = None,
        redmine_priority_id: str | None = None,
        redmine_severity: str | None = None,
        redmine_custom_priority: Any = None,
        redmine_template_file: str | None = None,
        redmine_custom_fields: Any = None,
    ) -> dict[str, Any]:
        validated_context = validate_operation_context(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "correlation_id": correlation_id or operation_id,
                "environment": environment,
                "requested_at": utc_now_iso(),
                "source": "qa-integration-agent",
            }
        )
        operation_id = validated_context["operation_id"]
        correlation_id = validated_context["correlation_id"]
        environment = validated_context["environment"]
        for name, value in (("project", project), ("plan", plan), ("platform", platform), ("build", build)):
            if not str(value or "").strip():
                raise CoordinatorError(f"{name} is required.", code="INVALID_ARGUMENT")
        report_path = Path(report)
        if not report_path.exists():
            raise CoordinatorError(f"Report file does not exist: {report_path}", code="REPORT_NOT_FOUND")
        if skip_policy not in {"ignore", "blocked"}:
            raise CoordinatorError("skip_policy must be ignore or blocked.", code="INVALID_ARGUMENT")
        header, parsed = parse_report(report_path)
        report_schema = str(header.get(SCHEMA_HEADER_KEY) or "")
        writable: list[dict[str, Any]] = []
        ignored: list[dict[str, Any]] = []
        for parsed_result in parsed:
            row = result_to_dict(parsed_result)
            if parsed_result.status is None and parsed_result.raw_status.casefold() in {"skip", "skipped"}:
                if skip_policy == "blocked":
                    row["status"] = "b"
                    writable.append(row)
                else:
                    ignored.append(row)
            elif parsed_result.status in {"p", "f", "b"}:
                writable.append(row)
            else:
                ignored.append(row)
        duplicates = sorted(
            external_id
            for external_id in {row["external_id"] for row in writable}
            if sum(1 for row in writable if row["external_id"] == external_id) > 1
        )
        if duplicates:
            raise CoordinatorError("Duplicate testcase ids: " + ", ".join(duplicates), code="DUPLICATE_CASE")
        context = {
            "project": {"name": project},
            "plan": {"name": plan},
            "platform": {"name": platform},
            "build": {"name": build},
        }
        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        for row in writable:
            marker: str | None = None
            digest: str | None = None
            redmine_request: dict[str, Any] | None = None
            redmine_preview: dict[str, Any] | None = None
            if row["status"] == "f" and redmine_create_bugs:
                if not redmine_project_id:
                    raise CoordinatorError(
                        "redmine_project_id is required when Redmine bug creation is enabled.",
                        code="REDMINE_TARGET_REQUIRED",
                    )
                # Reuse the stable policy implementation while the contract remains v1.
                parsed_result = next(value for value in parsed if value.external_id == row["external_id"])
                key = build_dedupe_key(
                    redmine_project_id=redmine_project_id,
                    context=context,
                    result=parsed_result,
                )
                digest = dedupe_digest(key)
                marker = dedupe_marker(digest)
                redmine_request = {
                    "operation_id": child_operation_id(operation_id, row["external_id"], "redmine"),
                    "environment": environment,
                    "project_id": redmine_project_id,
                    "subject": f"[{row['external_id']}] {row['test_name']} Result {row['raw_status']}",
                    "description": self._redmine_description(
                        operation_id=operation_id,
                        report_path=report_path,
                        project=project,
                        plan=plan,
                        platform=platform,
                        build=build,
                        result=row,
                        marker=marker,
                    ),
                    "tracker_id": redmine_tracker_id,
                    "priority_id": redmine_priority_id,
                    "severity": redmine_severity,
                    "custom_priority": redmine_custom_priority,
                    "template_file": redmine_template_file,
                    "custom_fields": render_custom_fields(
                        template_file=redmine_template_file,
                        custom_fields=redmine_custom_fields,
                        header=header,
                        result=row,
                        context=context,
                    ),
                    "dedupe_marker": marker,
                }
                redmine_preview = require_result(
                    self.ports.redmine_bug(**redmine_request),
                    f"Redmine preview {row['external_id']}",
                )
                if redmine_preview["action"] == "blocked":
                    warnings.extend(redmine_preview.get("warnings") or [])
            base_notes = self._base_notes(
                operation_id=operation_id,
                report_path=report_path,
                report_schema=report_schema,
                project=project,
                plan=plan,
                platform=platform,
                build=build,
                result=row,
                dedupe_marker_value=marker,
            )
            testlink_request = {
                "operation_id": child_operation_id(operation_id, row["external_id"], "testlink"),
                "environment": environment,
                "project": project,
                "plan": plan,
                "platform": platform,
                "build": build,
                "testcase_external_id": row["external_id"],
                "status": row["status"],
                "notes": base_notes,
                "execution_duration": (
                    float(row["duration_seconds"]) / 60.0
                    if row.get("duration_seconds") is not None
                    else None
                ),
            }
            testlink_preview = require_result(
                self.ports.testlink_execution(**testlink_request),
                f"TestLink preview {row['external_id']}",
            )
            items.append(
                {
                    "result": row,
                    "dedupe_marker": marker,
                    "dedupe_digest": digest,
                    "redmine_request": redmine_request,
                    "redmine_preview": redmine_preview,
                    "testlink_request": testlink_request,
                    "testlink_preview": testlink_preview,
                }
            )
        plan_payload = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": operation_id,
            "correlation_id": correlation_id or operation_id,
            "environment": environment,
            "input_digest": file_sha256(report_path),
            "report": str(report_path),
            "report_schema": report_schema,
            "target": {"project": project, "plan": plan, "platform": platform, "build": build},
            "redmine_create_bugs": bool(redmine_create_bugs),
            "items": items,
            "ignored": ignored,
            "warnings": warnings,
        }
        plan_payload["preview_digest"] = payload_digest(plan_payload)
        return plan_payload

    @staticmethod
    def public_preview(plan: dict[str, Any], *, include_items: bool = True) -> dict[str, Any]:
        public_items = []
        blocked = False
        for item in plan["items"]:
            redmine = item["redmine_preview"] or {}
            action = str(redmine.get("action") or "none")
            blocked = blocked or action == "blocked"
            public_item = {
                "testcase_external_id": item["result"]["external_id"],
                "test_name": item["result"]["test_name"],
                "raw_status": item["result"]["raw_status"],
                "status": item["result"]["status"],
                "testlink_planned_write": True,
                "testlink_preview_digest": item["testlink_preview"]["preview_digest"],
                "redmine_action": action,
                "redmine_preview_digest": redmine.get("preview_digest"),
                "redmine_issue_fields": redmine.get("issue_fields"),
                "redmine_issue_payload": redmine.get("issue_payload"),
                "dedupe_digest": item["dedupe_digest"],
            }
            if redmine.get("existing_issue") is not None:
                public_item["existing_issue"] = redmine["existing_issue"]
            public_items.append(public_item)
        preview = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": plan["operation_id"],
            "correlation_id": plan["correlation_id"],
            "environment": plan["environment"],
            "mode": "preview",
            "preview_digest": plan["preview_digest"],
            "planned_write": bool(plan["items"]) and not blocked,
            "input_digest": plan["input_digest"],
            "report_schema": plan["report_schema"],
            "target": plan["target"],
            "parsed_count": len(plan["items"]) + len(plan["ignored"]),
            "write_count": len(plan["items"]),
            "ignored_count": len(plan["ignored"]),
            "warnings": plan["warnings"] if include_items else plan["warnings"][:20],
            "warning_count": len(plan["warnings"]),
            "warnings_truncated": not include_items and len(plan["warnings"]) > 20,
            "summary": {
                "status_counts": {
                    status: sum(1 for item in public_items if item["status"] == status)
                    for status in sorted({item["status"] for item in public_items})
                },
                "redmine_action_counts": {
                    action: sum(1 for item in public_items if item["redmine_action"] == action)
                    for action in sorted({item["redmine_action"] for item in public_items})
                },
                "sample_testcase_external_ids": [
                    item["testcase_external_id"] for item in public_items[:10]
                ],
            },
        }
        if include_items:
            preview["items"] = public_items
        return preview

    @staticmethod
    def _workflow_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "report_schema": plan["report_schema"],
            "project": plan["target"]["project"],
            "plan": plan["target"]["plan"],
            "platform": plan["target"]["platform"],
            "build": plan["target"]["build"],
            "redmine_create_bugs": plan["redmine_create_bugs"],
        }

    @staticmethod
    def _audit_item_from_plan(item: dict[str, Any]) -> dict[str, Any]:
        redmine = item["redmine_preview"] or {}
        return {
            "testcase_external_id": item["result"]["external_id"],
            "state": "previewed",
            "redmine_action": "blocked" if redmine.get("action") == "blocked" else "none",
            "redmine_issue_id": None,
            "redmine_issue_url": None,
            "dedupe_digest": item["dedupe_digest"],
            "redmine_preview_digest": redmine.get("preview_digest"),
            "planned_redmine_action": str(redmine.get("action") or "none"),
            "redmine_audit_id": None,
            "redmine_field_verification": None,
            "testlink_write": "pending",
            "testlink_execution_id": None,
            "testlink_audit_id": None,
            "testlink_target_digest": payload_digest(item["testlink_preview"]["target"]),
            "evidence_comment": "pending" if redmine else "not-required",
            "evidence_audit_id": None,
            "errors": [],
            "resolved_errors": [],
        }

    @staticmethod
    def _safe_issue_from_audit(item: dict[str, Any]) -> dict[str, Any] | None:
        issue_id = item.get("redmine_issue_id")
        issue_url = item.get("redmine_issue_url")
        if issue_id in (None, "") or issue_url in (None, ""):
            return None
        return {
            "id": str(issue_id),
            "url": str(issue_url),
            "subject": "",
            "reused": item.get("redmine_action") == "reused",
        }

    def execute_plan(
        self,
        plan: dict[str, Any],
        *,
        confirmed_preview_digest: str,
        report: str,
        audit_dir: str = DEFAULT_AUDIT_DIR,
        resume_audit: str | None = None,
    ) -> dict[str, Any]:
        report_path = Path(report)
        blocked_items = [
            item["result"]["external_id"]
            for item in plan["items"]
            if (item["redmine_preview"] or {}).get("action") == "blocked"
        ]
        if blocked_items:
            raise CoordinatorError(
                "Redmine policy blocks these testcases: " + ", ".join(blocked_items),
                code="WRITE_BLOCKED",
            )

        audit_path: Path
        if resume_audit:
            audit = read_workflow_audit(resume_audit)
            if audit.get("input_digest") != plan["input_digest"]:
                raise CoordinatorError("Resume report digest does not match.", code="RESUME_MISMATCH")
            if audit.get("environment") != plan["environment"]:
                raise CoordinatorError("Resume environment does not match.", code="RESUME_MISMATCH")
            if audit.get("workflow") != self._workflow_from_plan(plan):
                raise CoordinatorError("Resume workflow target does not match.", code="RESUME_MISMATCH")
            if audit.get("preview_digest") != confirmed_preview_digest:
                raise CoordinatorError("Resume preview digest does not match audit.", code="RESUME_MISMATCH")
            audit_path = Path(resume_audit)
            previous_by_id = {
                str(item.get("testcase_external_id")): item
                for item in audit.get("items") or []
                if isinstance(item, dict)
            }
            for plan_item in plan["items"]:
                previous = previous_by_id.get(plan_item["result"]["external_id"])
                if previous and previous.get("testlink_target_digest") != payload_digest(
                    plan_item["testlink_preview"]["target"]
                ):
                    raise CoordinatorError(
                        f"Resume TestLink target changed for {plan_item['result']['external_id']}.",
                        code="RESUME_MISMATCH",
                    )
                if previous and not previous.get("redmine_issue_id"):
                    current_redmine = plan_item["redmine_preview"] or {}
                    if previous.get("redmine_preview_digest") != current_redmine.get("preview_digest"):
                        safe_create_to_reuse = (
                            previous.get("planned_redmine_action") == "create"
                            and current_redmine.get("action") == "reuse"
                            and previous.get("dedupe_digest") == plan_item.get("dedupe_digest")
                        )
                        if not safe_create_to_reuse:
                            raise CoordinatorError(
                                f"Resume Redmine decision changed for {plan_item['result']['external_id']}.",
                                code="RESUME_MISMATCH",
                            )
        else:
            if confirmed_preview_digest != plan["preview_digest"]:
                raise CoordinatorError(
                    "Coordinator plan does not match the confirmed preview digest.",
                    code="PREVIEW_MISMATCH",
                )
            created_at = utc_now_iso()
            audit = {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": plan["operation_id"],
                "correlation_id": plan["correlation_id"],
                "environment": plan["environment"],
                "status": "user-confirmed",
                "preview_digest": confirmed_preview_digest,
                "input_digest": plan["input_digest"],
                "created_at": created_at,
                "updated_at": created_at,
                "workflow": self._workflow_from_plan(plan),
                "items": [self._audit_item_from_plan(item) for item in plan["items"]],
                "errors": [],
                "resolved_errors": [],
            }
            audit_path = write_workflow_audit(audit, audit_dir)
            previous_by_id = {}

        audit_items = {
            str(item["testcase_external_id"]): item
            for item in audit["items"]
        }

        def persist() -> None:
            audit["updated_at"] = utc_now_iso()
            write_workflow_audit(audit, audit_path.parent, audit_id=audit_path.name)

        for plan_item in plan["items"]:
            result = plan_item["result"]
            external_id = result["external_id"]
            item_audit = audit_items[external_id]
            previous = previous_by_id.get(external_id)
            issue = self._safe_issue_from_audit(previous or item_audit)
            marker = plan_item["dedupe_marker"]

            if previous and item_audit.get("errors"):
                item_audit.setdefault("resolved_errors", []).extend(item_audit["errors"])
                item_audit["errors"] = []
                still_active: list[dict[str, Any]] = []
                for existing_error in audit.get("errors") or []:
                    if existing_error.get("target") == external_id:
                        audit.setdefault("resolved_errors", []).append(existing_error)
                    else:
                        still_active.append(existing_error)
                audit["errors"] = still_active

            if previous and previous.get("testlink_write") in {"success", "skipped-resume"}:
                item_audit["testlink_write"] = "skipped-resume"
                item_audit["state"] = "testlink-written"
                if previous.get("redmine_action") == "reused" and previous.get("evidence_comment") == "failed":
                    try:
                        comment_request = {
                            "operation_id": child_operation_id(plan["operation_id"], external_id, "comment"),
                            "environment": plan["environment"],
                            "issue_id": str(previous["redmine_issue_id"]),
                            "notes": self._evidence_comment(
                                operation_id=plan["operation_id"],
                                project=plan["target"]["project"],
                                plan=plan["target"]["plan"],
                                platform=plan["target"]["platform"],
                                build=plan["target"]["build"],
                                result=result,
                                report_path=report_path,
                                marker=str(marker or ""),
                                execution_id=str(previous.get("testlink_execution_id") or ""),
                            ),
                        }
                        comment_preview = require_result(
                            self.ports.redmine_comment(**comment_request),
                            f"Redmine comment preview {external_id}",
                        )
                        comment_result = require_result(
                            self.ports.redmine_comment(
                                **comment_request,
                                write=True,
                                preview_digest=comment_preview["preview_digest"],
                            ),
                            f"Redmine comment write {external_id}",
                        )
                        item_audit["evidence_comment"] = "added"
                        item_audit["evidence_audit_id"] = comment_result.get("audit_id")
                        item_audit["state"] = "completed"
                    except Exception as exc:
                        error = normalize_error(exc, "redmine-comment")
                        item_audit["errors"].append(error)
                        audit["errors"].append({**error, "target": external_id})
                        item_audit["evidence_comment"] = "failed"
                        item_audit["state"] = "partial-failure"
                else:
                    item_audit["state"] = "completed"
                persist()
                continue

            try:
                if issue is None and plan_item["redmine_request"] is not None:
                    redmine_preview = plan_item["redmine_preview"]
                    redmine_response = self.ports.redmine_bug(
                        **plan_item["redmine_request"],
                        write=True,
                        preview_digest=redmine_preview["preview_digest"],
                    )
                    if not redmine_response.get("ok"):
                        response_error = redmine_response.get("error")
                        partial = response_error.get("partial_result") if isinstance(response_error, dict) else None
                        partial_issue = partial.get("issue") if isinstance(partial, dict) else None
                        if isinstance(partial_issue, dict) and partial_issue.get("id") and partial_issue.get("url"):
                            issue = partial_issue
                            item_audit["redmine_action"] = str(partial.get("action") or "created")
                            item_audit["redmine_issue_id"] = issue["id"]
                            item_audit["redmine_issue_url"] = issue["url"]
                            item_audit["redmine_audit_id"] = partial.get("audit_id")
                            item_audit["redmine_field_verification"] = partial.get("field_verification")
                            item_audit["state"] = "redmine-verification-failed"
                            persist()
                    redmine_result = require_result(redmine_response, f"Redmine write {external_id}")
                    issue = redmine_result["issue"]
                    item_audit["redmine_action"] = redmine_result["action"]
                    item_audit["redmine_issue_id"] = issue["id"]
                    item_audit["redmine_issue_url"] = issue["url"]
                    item_audit["redmine_audit_id"] = redmine_result.get("audit_id")
                    item_audit["redmine_field_verification"] = redmine_result.get("field_verification")
                    item_audit["state"] = "redmine-resolved"
                    persist()
                    if redmine_result["action"] == "created" and not (
                        isinstance(redmine_result.get("field_verification"), dict)
                        and redmine_result["field_verification"].get("verified") is True
                    ):
                        raise CoordinatorError(
                            f"Redmine field readback verification is missing for {external_id}.",
                            code="REDMINE_VERIFICATION_REQUIRED",
                        )
                if issue is not None and item_audit.get("redmine_action") == "created" and not (
                    isinstance(item_audit.get("redmine_field_verification"), dict)
                    and item_audit["redmine_field_verification"].get("verified") is True
                ):
                    raise CoordinatorError(
                        f"Redmine field readback verification is unresolved for {external_id}.",
                        code="REDMINE_VERIFICATION_REQUIRED",
                    )
                final_request = {
                    **plan_item["testlink_request"],
                    "notes": self._final_notes(plan_item["testlink_request"]["notes"], issue),
                }
                final_preview = require_result(
                    self.ports.testlink_execution(**final_request),
                    f"TestLink final preview {external_id}",
                )
                if payload_digest(final_preview["target"]) != item_audit["testlink_target_digest"]:
                    raise CoordinatorError(
                        f"Resolved TestLink target changed for {external_id}.",
                        code="TARGET_CHANGED",
                    )
                testlink_result = require_result(
                    self.ports.testlink_execution(
                        **final_request,
                        write=True,
                        preview_digest=final_preview["preview_digest"],
                    ),
                    f"TestLink write {external_id}",
                )
                item_audit["testlink_write"] = "success"
                item_audit["testlink_execution_id"] = testlink_result.get("execution_id")
                item_audit["testlink_audit_id"] = testlink_result.get("audit_id")
                item_audit["state"] = "testlink-written"
                persist()
                if issue and issue.get("reused"):
                    comment_request = {
                        "operation_id": child_operation_id(plan["operation_id"], external_id, "comment"),
                        "environment": plan["environment"],
                        "issue_id": issue["id"],
                        "notes": self._evidence_comment(
                            operation_id=plan["operation_id"],
                            project=plan["target"]["project"],
                            plan=plan["target"]["plan"],
                            platform=plan["target"]["platform"],
                            build=plan["target"]["build"],
                            result=result,
                            report_path=report_path,
                            marker=str(marker or ""),
                            execution_id=str(testlink_result.get("execution_id") or ""),
                        ),
                    }
                    comment_preview = require_result(
                        self.ports.redmine_comment(**comment_request),
                        f"Redmine comment preview {external_id}",
                    )
                    comment_result = require_result(
                        self.ports.redmine_comment(
                            **comment_request,
                            write=True,
                            preview_digest=comment_preview["preview_digest"],
                        ),
                        f"Redmine comment write {external_id}",
                    )
                    item_audit["evidence_comment"] = "added"
                    item_audit["evidence_audit_id"] = comment_result.get("audit_id")
                    item_audit["state"] = "evidence-written"
                else:
                    item_audit["evidence_comment"] = "not-required"
                item_audit["state"] = "completed"
                persist()
            except Exception as exc:
                stage = "testlink" if item_audit["testlink_write"] != "success" else "redmine-comment"
                error = normalize_error(exc, stage)
                item_audit["errors"].append(error)
                audit["errors"].append({**error, "target": external_id})
                if item_audit["testlink_write"] != "success":
                    item_audit["testlink_write"] = "failed"
                if item_audit["testlink_write"] == "success" and issue and issue.get("reused"):
                    item_audit["evidence_comment"] = "failed"
                item_audit["state"] = "partial-failure"
                persist()

        audit["status"] = "partial-failure" if audit["errors"] else "completed"
        persist()
        return {
            "status": audit["status"],
            "audit_id": audit_path.name,
            "audit": audit,
        }

    @staticmethod
    def validate_traceability(audit: dict[str, Any]) -> dict[str, Any]:
        issues: list[str] = []
        for item in audit.get("items") or []:
            external_id = str(item.get("testcase_external_id") or "")
            action = item.get("redmine_action")
            if action in {"created", "reused"}:
                if not item.get("redmine_issue_id") or not item.get("redmine_issue_url"):
                    issues.append(f"{external_id}: missing Redmine identity")
                if item.get("testlink_write") not in {"success", "skipped-resume"}:
                    issues.append(f"{external_id}: missing successful TestLink execution")
            if action == "created" and not (
                isinstance(item.get("redmine_field_verification"), dict)
                and item["redmine_field_verification"].get("verified") is True
            ):
                issues.append(f"{external_id}: Redmine field readback is not verified")
            if action == "reused" and item.get("testlink_write") in {"success", "skipped-resume"}:
                if item.get("evidence_comment") != "added":
                    issues.append(f"{external_id}: reused issue missing evidence comment")
        return {"valid": not issues, "issues": issues}
