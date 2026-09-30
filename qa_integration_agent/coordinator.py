from __future__ import annotations

import contextlib
import functools
import hashlib
from pathlib import Path
from typing import Any, Callable

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


def execution_duration_minutes(duration_seconds: float | None) -> float | None:
    """TestLink execduration is minutes; keep the legacy upload rounding."""
    if duration_seconds is None:
        return None
    return round(float(duration_seconds) / 60.0, 4)


def in_port_session(method: Callable[..., Any]) -> Callable[..., Any]:
    """Run one batch inside a single port session when the ports support it.

    StdioMcpPorts then keeps one child per MCP server for the whole preview or
    execute batch instead of starting one per testcase. Every item still makes
    its own preview/write call with its own child operation id and digest.
    """

    @functools.wraps(method)
    def wrapper(self: "QaCoordinator", *args: Any, **kwargs: Any) -> Any:
        session = getattr(self.ports, "session", None)
        with session() if callable(session) else contextlib.nullcontext():
            return method(self, *args, **kwargs)

    return wrapper


# A plan built from `reports` (one report per node) uses contracts v2; a plan
# built from a single `report` keeps the v1 shape and digest byte for byte.
MULTI_REPORT_SCHEMA_VERSION = "2.0"
SUPPORTED_PLAN_SCHEMA_VERSIONS = (CONTRACT_SCHEMA_VERSION, MULTI_REPORT_SCHEMA_VERSION)
REPORT_HEADER_FIELDS = (
    "Report generated on",
    "EMS Version",
    "Node Name",
    "Node IP",
    "Node Chassis",
    "Summary",
    "Total test time",
)
MAX_REPORT_LABEL_LENGTH = 64
SKIP_RAW_STATUSES = {"skip", "skipped"}


def reports_input_digest(reports: list[dict[str, Any]]) -> str:
    """Canonical digest over every report's label and file hash, in review order."""
    return payload_digest(
        {"reports": [{"label": str(entry["label"]), "sha256": str(entry["sha256"])} for entry in reports]}
    )


def _validated_report_list(reports: Any) -> list[tuple[str, Path]]:
    if not isinstance(reports, list) or not reports:
        raise CoordinatorError("reports must be a non-empty array of {label, path}.", code="INVALID_ARGUMENT")
    entries: list[tuple[str, Path]] = []
    labels: set[str] = set()
    paths: set[str] = set()
    for index, entry in enumerate(reports):
        if not isinstance(entry, dict) or set(entry) - {"label", "path"}:
            raise CoordinatorError(
                f"reports[{index}] must be an object with only label and path.", code="INVALID_ARGUMENT"
            )
        label = entry.get("label")
        path = entry.get("path")
        if not isinstance(label, str) or not label.strip():
            raise CoordinatorError(f"reports[{index}].label must be a non-empty string.", code="INVALID_ARGUMENT")
        label = label.strip()
        if len(label) > MAX_REPORT_LABEL_LENGTH or any(ord(char) < 32 for char in label):
            raise CoordinatorError(
                f"reports[{index}].label must be at most {MAX_REPORT_LABEL_LENGTH} printable characters.",
                code="INVALID_ARGUMENT",
            )
        if label.casefold() in labels:
            raise CoordinatorError(f"reports labels must be unique: {label}", code="INVALID_ARGUMENT")
        labels.add(label.casefold())
        if not isinstance(path, str) or not path.strip():
            raise CoordinatorError(f"reports[{index}].path must be a non-empty string.", code="INVALID_ARGUMENT")
        report_path = Path(path)
        if not report_path.exists():
            raise CoordinatorError(f"Report file does not exist for {label}: {report_path}", code="REPORT_NOT_FOUND")
        resolved = str(report_path.resolve()).casefold()
        if resolved in paths:
            raise CoordinatorError(f"reports[{index}] repeats a report file: {report_path}", code="INVALID_ARGUMENT")
        paths.add(resolved)
        entries.append((label, report_path))
    return entries


def _node_entry(label: str, report_path: Path, header: dict[str, str], parsed: Any) -> dict[str, Any]:
    return {
        "label": label,
        "report_file": report_path.name,
        "raw_status": parsed.raw_status,
        "status": parsed.status,
        "duration": parsed.duration_text,
        "duration_seconds": parsed.duration_seconds,
        "node_name": header.get("Node Name", ""),
        "node_ip": header.get("Node IP", ""),
        "node_chassis": header.get("Node Chassis", ""),
    }


def node_result_lines(nodes: list[dict[str, Any]] | None) -> list[str]:
    """One traceability line per node report, in review order."""
    lines = []
    for node in nodes or []:
        target = " / ".join(
            str(node.get(key) or "-") for key in ("node_name", "node_ip", "node_chassis")
        )
        lines.append(
            f"{node['label']}: Result {node['raw_status']}; duration {node.get('duration') or '-'}; "
            f"target {target}; report {node['report_file']}"
        )
    return lines


def _row_status(parsed: Any, skip_policy: str) -> str | None:
    """Single-report status rule; None means the row is ignored."""
    if parsed.status is None and parsed.raw_status.casefold() in SKIP_RAW_STATUSES:
        return "b" if skip_policy == "blocked" else None
    return parsed.status if parsed.status in {"p", "f", "b"} else None


class QaCoordinator:
    def __init__(self, ports: IntegrationPorts | None = None):
        self.ports = ports or StdioMcpPorts()

    @staticmethod
    def _base_notes(
        *,
        operation_id: str,
        report_file: str,
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
            f"Report File: {report_file}",
            f"TestLink Project: {project}",
            f"Test Plan: {plan}",
            f"Platform: {platform}",
            f"Build: {build}",
            f"Test Case: {result['external_id']}",
            f"Automation Test Function: {result['test_name']}",
            f"Result: {result['raw_status']}",
            *QaCoordinator._node_block(result),
        ]
        if dedupe_marker_value:
            lines.append(f"Dedupe Key: {dedupe_marker_value}")
        return "\n".join(lines)

    @staticmethod
    def _redmine_description(
        *,
        operation_id: str,
        report_file: str,
        project: str,
        plan: str,
        platform: str,
        build: str,
        result: dict[str, Any],
        marker: str,
    ) -> str:
        failing = [node["label"] for node in result.get("nodes") or [] if node.get("status") == "f"]
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
                *([f"Failing Nodes: {', '.join(failing)}"] if failing else []),
                *QaCoordinator._node_block(result),
                f"Report File: {report_file}",
                "Execution URL:",
                f"Dedupe Key: {marker}",
            ]
        )

    @staticmethod
    def _node_block(result: dict[str, Any]) -> list[str]:
        lines = node_result_lines(result.get("nodes"))
        return ["Node Results:", *lines] if lines else []

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
        report_file: str,
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
                *QaCoordinator._node_block(result),
                f"Report File: {report_file}",
                f"TestLink Execution ID: {execution_id or ''}",
                f"Dedupe Key: {marker}",
                "This comment does not change Redmine status, assignee, or fixed version.",
            ]
        )

    @in_port_session
    def build_plan(
        self,
        *,
        operation_id: str,
        correlation_id: str | None = None,
        environment: str,
        project: str,
        plan: str,
        platform: str,
        build: str,
        report: str | None = None,
        reports: list[dict[str, Any]] | None = None,
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
        """Plan one TestLink execution per testcase from one report or one report per node.

        `report` keeps the v1 plan. `reports` ([{label, path}], one per node)
        builds a v2 plan whose items aggregate every node's result for the
        testcase; see _aggregate_node_reports for the rules.
        """
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
        multi = reports is not None
        if multi == (report not in (None, "")):
            raise CoordinatorError("Provide exactly one of report or reports.", code="INVALID_ARGUMENT")
        if multi:
            report_entries = _validated_report_list(reports)
        else:
            report_path = Path(str(report))
            if not report_path.exists():
                raise CoordinatorError(f"Report file does not exist: {report_path}", code="REPORT_NOT_FOUND")
            report_entries = [("", report_path)]
        if skip_policy not in {"ignore", "blocked"}:
            raise CoordinatorError("skip_policy must be ignore or blocked.", code="INVALID_ARGUMENT")
        sources: list[dict[str, Any]] = []
        for label, path in report_entries:
            header, parsed = parse_report(path)
            sources.append({"label": label, "path": path, "header": header, "parsed": parsed})
        warnings: list[str] = []
        if multi:
            writable, ignored, leads = self._aggregate_node_reports(sources, skip_policy, warnings)
        else:
            writable, ignored, leads = self._single_report_rows(sources[0], skip_policy)
        report_file = ", ".join(source["path"].name for source in sources)
        report_schema = ", ".join(
            dict.fromkeys(str(source["header"].get(SCHEMA_HEADER_KEY) or "") for source in sources)
        )
        context = {
            "project": {"name": project},
            "plan": {"name": plan},
            "platform": {"name": platform},
            "build": {"name": build},
        }
        items: list[dict[str, Any]] = []
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
                # Dedupe and template tokens use the first failing node's result and
                # header (the only one for a single report); the description lists all nodes.
                lead_header, lead_parsed, lead_result = leads[row["external_id"]]
                key = build_dedupe_key(
                    redmine_project_id=redmine_project_id,
                    context=context,
                    result=lead_parsed,
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
                        report_file=report_file,
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
                        header=lead_header,
                        result=lead_result if lead_result is not None else row,
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
                report_file=report_file,
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
                "execution_duration": execution_duration_minutes(row.get("duration_seconds")),
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
        target = {"project": project, "plan": plan, "platform": platform, "build": build}
        if multi:
            report_records = [
                {
                    "label": source["label"],
                    "path": str(source["path"]),
                    "report_file": source["path"].name,
                    "sha256": file_sha256(source["path"]),
                    "report_schema": str(source["header"].get(SCHEMA_HEADER_KEY) or ""),
                    "header": {
                        key: source["header"][key] for key in REPORT_HEADER_FIELDS if key in source["header"]
                    },
                }
                for source in sources
            ]
            plan_payload = {
                "schema_version": MULTI_REPORT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "correlation_id": correlation_id or operation_id,
                "environment": environment,
                "input_digest": reports_input_digest(report_records),
                "reports": report_records,
                "target": target,
                "redmine_create_bugs": bool(redmine_create_bugs),
                "items": items,
                "ignored": ignored,
                "warnings": warnings,
            }
        else:
            plan_payload = {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "correlation_id": correlation_id or operation_id,
                "environment": environment,
                "input_digest": file_sha256(report_path),
                "report": str(report_path),
                "report_schema": report_schema,
                "target": target,
                "redmine_create_bugs": bool(redmine_create_bugs),
                "items": items,
                "ignored": ignored,
                "warnings": warnings,
            }
        plan_payload["preview_digest"] = payload_digest(plan_payload)
        return plan_payload

    @staticmethod
    def _single_report_rows(
        source: dict[str, Any],
        skip_policy: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, tuple[dict[str, str], Any, None]]]:
        writable: list[dict[str, Any]] = []
        ignored: list[dict[str, Any]] = []
        leads: dict[str, tuple[dict[str, str], Any, None]] = {}
        for parsed_result in source["parsed"]:
            row = result_to_dict(parsed_result)
            row["duration_seconds"] = parsed_result.duration_seconds
            status = _row_status(parsed_result, skip_policy)
            if status is None:
                ignored.append(row)
            else:
                row["status"] = status
                writable.append(row)
            # The v1 plan keyed dedupe on the first parsed row with the id; keep that.
            leads.setdefault(parsed_result.external_id, (source["header"], parsed_result, None))
        duplicates = sorted(
            external_id
            for external_id in {row["external_id"] for row in writable}
            if sum(1 for row in writable if row["external_id"] == external_id) > 1
        )
        if duplicates:
            raise CoordinatorError("Duplicate testcase ids: " + ", ".join(duplicates), code="DUPLICATE_CASE")
        return writable, ignored, leads

    @staticmethod
    def _aggregate_node_reports(
        sources: list[dict[str, Any]],
        skip_policy: str,
        warnings: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, tuple[dict[str, str], Any, dict[str, Any]]]]:
        """Merge one report per node into one TestLink result per testcase.

        Every report must contain the same testcases, each once. Status: any
        node f (Fail/Error) -> f; else any p -> p; else any b -> b; else, when
        every node skipped, skip_policy decides (ignore -> ignored, blocked -> b).
        The duration is the longest node duration. The lead node, whose result
        and header feed Redmine dedupe and templates, is the first node in
        `reports` order with the winning status.
        """
        by_report: list[dict[str, Any]] = []
        for source in sources:
            cases: dict[str, Any] = {}
            duplicates: set[str] = set()
            for parsed in source["parsed"]:
                if parsed.external_id in cases:
                    duplicates.add(parsed.external_id)
                cases.setdefault(parsed.external_id, parsed)
            if duplicates:
                raise CoordinatorError(
                    f"Duplicate testcase ids in report {source['label']}: " + ", ".join(sorted(duplicates)),
                    code="DUPLICATE_CASE",
                )
            by_report.append(cases)
        order = list(dict.fromkeys(external_id for cases in by_report for external_id in cases))
        missing = []
        for source, cases in zip(sources, by_report):
            absent = [external_id for external_id in order if external_id not in cases]
            if absent:
                shown = ", ".join(absent[:10]) + (f" (+{len(absent) - 10} more)" if len(absent) > 10 else "")
                missing.append(f"{source['label']} lacks {shown}")
        if missing:
            raise CoordinatorError(
                "Every report must contain the same testcases; " + "; ".join(missing),
                code="INVALID_ARGUMENT",
            )
        writable: list[dict[str, Any]] = []
        ignored: list[dict[str, Any]] = []
        leads: dict[str, tuple[dict[str, str], Any, dict[str, Any]]] = {}
        for external_id in order:
            pairs = [(source, cases[external_id]) for source, cases in zip(sources, by_report)]
            statuses = [parsed.status for _, parsed in pairs]
            status: str | None = next((value for value in ("f", "p", "b") if value in statuses), None)
            if status is not None:
                lead_index = statuses.index(status)
            else:
                lead_index = 0
                all_skipped = all(
                    parsed.status is None and parsed.raw_status.casefold() in SKIP_RAW_STATUSES
                    for _, parsed in pairs
                )
                status = "b" if all_skipped and skip_policy == "blocked" else None
            lead_source, lead = pairs[lead_index]
            row = result_to_dict(lead)
            row["test_name"] = pairs[0][1].test_name
            timed = [parsed for _, parsed in pairs if parsed.duration_seconds is not None]
            longest = max(timed, key=lambda parsed: parsed.duration_seconds) if timed else None
            row["duration"] = longest.duration_text if longest is not None else lead.duration_text
            row["duration_seconds"] = longest.duration_seconds if longest is not None else None
            row["nodes"] = [
                _node_entry(source["label"], source["path"], source["header"], parsed) for source, parsed in pairs
            ]
            names = list(dict.fromkeys(parsed.test_name for _, parsed in pairs))
            if len(names) > 1:
                warnings.append(f"{external_id}: automation test names differ across reports: " + ", ".join(names))
            if status is None:
                ignored.append(row)
            else:
                row["status"] = status
                writable.append(row)
            lead_result = result_to_dict(lead)
            lead_result["duration_seconds"] = lead.duration_seconds
            leads[external_id] = (lead_source["header"], lead, lead_result)
        return writable, ignored, leads

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
            if item["result"].get("nodes") is not None:
                public_item["nodes"] = [
                    {"label": node["label"], "raw_status": node["raw_status"], "duration": node["duration"]}
                    for node in item["result"]["nodes"]
                ]
            public_items.append(public_item)
        preview = {
            "schema_version": plan.get("schema_version", CONTRACT_SCHEMA_VERSION),
            "operation_id": plan["operation_id"],
            "correlation_id": plan["correlation_id"],
            "environment": plan["environment"],
            "mode": "preview",
            "preview_digest": plan["preview_digest"],
            "planned_write": bool(plan["items"]) and not blocked,
            "input_digest": plan["input_digest"],
            **(
                {"reports": [dict(entry) for entry in plan["reports"]]}
                if "reports" in plan
                else {"report_schema": plan["report_schema"]}
            ),
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
        if "reports" in plan:
            reports = {
                "reports": [
                    {key: entry[key] for key in ("label", "report_file", "sha256", "report_schema")}
                    for entry in plan["reports"]
                ]
            }
        else:
            reports = {"report_schema": plan["report_schema"]}
        return {
            **reports,
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

    @in_port_session
    def execute_plan(
        self,
        plan: dict[str, Any],
        *,
        confirmed_preview_digest: str,
        report: str | None = None,
        audit_dir: str = DEFAULT_AUDIT_DIR,
        resume_audit: str | None = None,
    ) -> dict[str, Any]:
        """Execute a reviewed plan; callers verify every report hash first (see api)."""
        if plan.get("schema_version", CONTRACT_SCHEMA_VERSION) not in SUPPORTED_PLAN_SCHEMA_VERSIONS:
            raise CoordinatorError("Unsupported coordinator plan schema.", code="PREVIEW_ARTIFACT_INVALID")
        if "reports" in plan:
            report_file = ", ".join(str(entry["report_file"]) for entry in plan["reports"])
        else:
            report_file = Path(str(report or plan.get("report") or "")).name
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
                "schema_version": plan.get("schema_version", CONTRACT_SCHEMA_VERSION),
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
                                report_file=report_file,
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
                            report_file=report_file,
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
