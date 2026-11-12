from __future__ import annotations

import difflib
import hashlib
import re
import threading
from pathlib import Path
from typing import Any, Callable

from qa_mcp_contracts import (
    CONTRACT_SCHEMA_VERSION,
    ContractError,
    assert_safe_contract,
    payload_digest,
    validate_preview_digest,
)

from .attachments import PreparedAttachment, attachment_warnings, prepare_attachments
from .audit import find_operation_audits, utc_now_iso, write_operation_audit
from .client import RedmineClient
from .config import DEFAULT_AUDIT_DIR, DEFAULT_TIMEOUT_SECONDS, RedmineSettings, load_redmine_settings
from .errors import (
    RedmineMcpError,
    mask_email_addresses,
    mask_embedded_credentials,
    normalize_error,
    redact_secrets,
)
from .models import RedmineIssue
from .policy import blocked_manager_fields, manager_fields_allowed, validate_environment
from .templates import load_template, merge_template_values, validate_template
from .text_format import validate_redmine_text


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOCKS_GUARD = threading.Lock()
_DEDUPE_LOCKS: dict[str, threading.Lock] = {}


def _dedupe_lock(marker: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _DEDUPE_LOCKS.setdefault(marker, threading.Lock())


def _runtime(
    env_file: str | None,
    timeout: int,
) -> tuple[RedmineSettings, RedmineClient]:
    settings = load_redmine_settings(env_file=env_file, timeout=timeout)
    return settings, RedmineClient(settings.url, settings.api_key, timeout=settings.timeout)


def _success(result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "code": 0, "result": redact_secrets(result)}


def _failure(operation_id: str, stage: str, error: BaseException) -> dict[str, Any]:
    normalized = normalize_error(error)
    return {
        "ok": False,
        "code": 1,
        "error": redact_secrets(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": str(operation_id or "unknown-operation"),
                "error": {
                    "code": normalized.code,
                    "message": normalized.message,
                    "stage": stage,
                    "retryable": normalized.retryable,
                },
            }
        ),
    }


def _require_text(name: str, value: Any, *, max_length: int | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        raise RedmineMcpError(f"{name} is required.", code="INVALID_ARGUMENT")
    if max_length is not None and len(text) > max_length:
        raise RedmineMcpError(f"{name} exceeds {max_length} characters.", code="INVALID_ARGUMENT")
    return text


def _normalize_custom_fields(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise RedmineMcpError("custom_fields must be an array.", code="INVALID_ARGUMENT")
    normalized: list[dict[str, Any]] = []
    for index, field in enumerate(value):
        if not isinstance(field, dict):
            raise RedmineMcpError(
                f"custom_fields[{index}] must be an object.",
                code="INVALID_ARGUMENT",
            )
        field_id = field.get("id")
        if field_id in (None, "") or "value" not in field:
            raise RedmineMcpError(
                f"custom_fields[{index}] requires id and value.",
                code="INVALID_ARGUMENT",
            )
        normalized.append({"id": field_id, "value": field.get("value")})
    return normalized


def _issue_preview(issue: RedmineIssue | None) -> dict[str, Any] | None:
    if issue is None:
        return None
    return {
        "id": issue.id,
        "url": issue.url,
        "subject": issue.subject,
        "state": issue.state,
    }


def _issue_result(issue: RedmineIssue | None) -> dict[str, Any] | None:
    if issue is None:
        return None
    return {
        "id": issue.id,
        "url": issue.url,
        "subject": issue.subject,
        "reused": issue.reused,
    }


def _normalized_field_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_normalized_field_value(item) for item in value]
    return str(value).strip()


def _verify_issue_fields(
    issue_data: dict[str, Any],
    issue_fields: dict[str, Any],
) -> dict[str, Any]:
    severity = issue_fields["severity"]
    priority = issue_fields.get("priority")
    actual_priority = issue_data.get("priority") if isinstance(issue_data.get("priority"), dict) else {}
    expected_severity_id = str(severity["priority_id"])
    actual_severity_id = str(actual_priority.get("id") or "")
    severity_result = {
        "label": severity.get("label"),
        "transport_field": "priority_id",
        "expected_priority_id": expected_severity_id,
        "actual_priority_id": actual_severity_id,
        "match": actual_severity_id == expected_severity_id,
    }
    priority_result: dict[str, Any] | None = None
    if priority is not None:
        field_id = str(priority["custom_field_id"])
        custom_fields = issue_data.get("custom_fields")
        matching = next(
            (
                field
                for field in custom_fields if isinstance(field, dict)
                if str(field.get("id") or "").strip() == field_id
            ),
            None,
        ) if isinstance(custom_fields, list) else None
        expected_value = _normalized_field_value(priority.get("value"))
        actual_value = _normalized_field_value(matching.get("value")) if matching is not None else None
        priority_result = {
            "transport_field": "custom_fields",
            "custom_field_id": field_id,
            "expected_value": expected_value,
            "actual_value": actual_value,
            "found": matching is not None,
            "match": matching is not None and actual_value == expected_value,
        }
    verified = severity_result["match"] and (priority_result is None or priority_result["match"])
    result = {
        "status": "verified" if verified else "verification_failed",
        "verified": verified,
        "severity": severity_result,
        "priority": priority_result,
    }
    assert_safe_contract(result)
    return result


def _build_issue_payload(
    *,
    project_id: str,
    subject: str,
    description: str,
    tracker_id: str,
    priority_id: str,
    custom_fields: Any = None,
    category_id: str | None = None,
    assigned_to_id: str | None = None,
    fixed_version_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_id": _require_text("project_id", project_id),
        "subject": _require_text("subject", subject, max_length=255),
        "description": _require_text("description", description),
        "tracker_id": _require_text("tracker_id", tracker_id),
        "priority_id": _require_text("priority_id", priority_id),
    }
    fields = _normalize_custom_fields(custom_fields)
    if fields:
        payload["custom_fields"] = fields
    optional = {
        "category_id": category_id,
        "assigned_to_id": assigned_to_id,
        "fixed_version_id": fixed_version_id,
    }
    for name, value in optional.items():
        if value not in (None, ""):
            payload[name] = str(value).strip()
    assert_safe_contract(payload)
    return payload


def _build_bug_plan(
    *,
    client: RedmineClient,
    operation_id: str,
    environment: str,
    issue_payload: dict[str, Any],
    issue_fields: dict[str, Any],
    text_format: dict[str, Any] | None,
    format_validation: dict[str, Any],
    attachments: list[dict[str, Any]],
    dedupe_marker: str,
    dedupe: str,
) -> dict[str, Any]:
    marker = _require_text("dedupe_marker", dedupe_marker, max_length=512)
    if dedupe != "open":
        raise RedmineMcpError(
            "Redmine bug creation requires open-issue dedupe.",
            code="DEDUPE_REQUIRED",
        )
    blocked_fields = blocked_manager_fields(issue_payload)
    open_issue: RedmineIssue | None = None
    closed_issue: RedmineIssue | None = None
    open_issue = client.find_issue_by_marker(
        project_id=str(issue_payload["project_id"]),
        marker=marker,
        status_id="open",
        tracker_id=str(issue_payload.get("tracker_id") or ""),
    )
    if open_issue is None:
        closed_issue = client.find_issue_by_marker(
            project_id=str(issue_payload["project_id"]),
            marker=marker,
            status_id="closed",
            tracker_id=str(issue_payload.get("tracker_id") or ""),
        )
    warnings: list[str] = []
    warnings.extend(
        str(item.get("message") or "Redmine text format warning.")
        for item in format_validation["warnings"]
    )
    warnings.extend(attachment_warnings(attachments))
    if not format_validation["valid"]:
        action = "blocked"
        existing_issue = open_issue or closed_issue
        warnings.append("Redmine description format validation failed.")
    elif blocked_fields:
        action = "blocked"
        existing_issue = open_issue or closed_issue
        warnings.append("Manager-only Redmine fields are blocked in this environment.")
    elif open_issue is not None:
        action = "reuse"
        existing_issue = open_issue
        if attachments:
            warnings.append(
                "The dedupe marker matches an open issue; attachments are not uploaded to reused issues "
                "(add them with redmine_add_comment instead)."
            )
    elif closed_issue is not None:
        action = "blocked"
        existing_issue = closed_issue
        warnings.append("A closed issue matches the dedupe marker; it will not be reopened automatically.")
    else:
        action = "create"
        existing_issue = None
    plan = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": _require_text("operation_id", operation_id, max_length=128),
        "environment": environment,
        "action": action,
        "planned_write": action == "create",
        "dedupe_marker": marker,
        "dedupe_digest": hashlib.sha256(marker.encode("utf-8")).hexdigest()[:16],
        "issue_payload": issue_payload,
        "issue_fields": issue_fields,
        "text_format": text_format,
        "format_validation": format_validation,
        "attachments": attachments,
        "attachment_action": (
            "upload-and-attach"
            if action == "create" and attachments
            else "not-uploaded-reused"
            if action == "reuse" and attachments
            else "none"
        ),
        "existing_issue": _issue_preview(existing_issue),
        "manager_fields_enabled": manager_fields_allowed(),
        "blocked_fields": blocked_fields,
        "warnings": warnings,
    }
    assert_safe_contract(plan)
    return plan


def _bug_preview(plan: dict[str, Any]) -> dict[str, Any]:
    result = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": plan["operation_id"],
        "environment": plan["environment"],
        "mode": "preview",
        "preview_digest": payload_digest(plan),
        "planned_write": plan["planned_write"],
        "action": plan["action"],
        "dedupe_digest": plan["dedupe_digest"],
        "manager_fields_enabled": plan["manager_fields_enabled"],
        "blocked_fields": plan["blocked_fields"],
        "warnings": plan["warnings"],
        "attachment_action": plan["attachment_action"],
        "attachment_count": len(plan["attachments"]),
        "attachments": plan["attachments"],
        "issue_fields": plan["issue_fields"],
        "text_format": plan["text_format"],
        "format_validation": plan["format_validation"],
        "issue_payload": plan["issue_payload"],
    }
    if plan["existing_issue"] is not None:
        result["existing_issue"] = plan["existing_issue"]
    return result


def _format_validation_audit(validation: dict[str, Any] | None) -> dict[str, Any] | None:
    if validation is None:
        return None
    return {
        "valid": bool(validation.get("valid")),
        "field": validation.get("field"),
        "engine": validation.get("engine"),
        "validation": validation.get("validation"),
        "policy_version": validation.get("policy_version"),
        "error_codes": [str(item.get("code") or "") for item in validation.get("errors") or []],
        "warning_codes": [str(item.get("code") or "") for item in validation.get("warnings") or []],
    }


def _attempt_failure_audit(
    *,
    operation_id: str,
    environment: str,
    action: str,
    preview_digest: str | None,
    error: BaseException,
    audit_dir: str | None,
    audit_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> str | None:
    normalized = normalize_error(error)
    status = "verification-failed" if normalized.code == "VERIFICATION_FAILED" else "failed"
    try:
        path = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": environment,
                "action": action,
                "status": status,
                "preview_digest": preview_digest,
                "finished_at": utc_now_iso(),
                "error": normalized.to_dict(),
                **(details or {}),
            },
            audit_dir,
            audit_id=audit_id,
        )
        return path.name
    except Exception:
        return None


def redmine_health(
    *,
    operation_id: str,
    environment: str,
    env_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        response = client.health()
        user = response.get("user") if isinstance(response.get("user"), dict) else {}
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "server": "redmine-mcp",
                "authenticated": True,
                "user_id": str(user.get("id") or ""),
                "login": str(user.get("login") or ""),
            }
        )
    except Exception as exc:
        return _failure(operation_id, "health", exc)


def _redact_content(value: Any, counts: dict[str, int]) -> Any:
    if isinstance(value, str):
        text, credentials = mask_embedded_credentials(value)
        text, emails = mask_email_addresses(text)
        counts["credentials"] += credentials
        counts["emails"] += emails
        return text
    if isinstance(value, dict):
        return {key: _redact_content(item, counts) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_content(item, counts) for item in value]
    return value


def _redacted(value: Any) -> tuple[Any, dict[str, int]]:
    counts = {"credentials": 0, "emails": 0}
    return _redact_content(value, counts), counts


def _custom_field_id(value: Any, name: str) -> str:
    text = str(value if value is not None else "").strip()
    if not text.isdigit():
        raise RedmineMcpError(f"{name} must be a numeric custom field ID.", code="INVALID_ARGUMENT")
    return text


def _custom_field_filters(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise RedmineMcpError("custom_field_filters must be an array.", code="INVALID_ARGUMENT")
    filters: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise RedmineMcpError(f"custom_field_filters[{index}] must be an object.", code="INVALID_ARGUMENT")
        field_id = _custom_field_id(item.get("id"), f"custom_field_filters[{index}].id")
        if any(existing["id"] == field_id for existing in filters):
            raise RedmineMcpError(f"custom field {field_id} is filtered more than once.", code="INVALID_ARGUMENT")
        match = str(item.get("match") or "exact").strip().casefold()
        if match not in {"exact", "contains"}:
            raise RedmineMcpError(
                f"custom_field_filters[{index}].match must be exact or contains.",
                code="INVALID_ARGUMENT",
            )
        filters.append(
            {
                "id": field_id,
                "value": _require_text(f"custom_field_filters[{index}].value", item.get("value")),
                "match": match,
            }
        )
    return filters


def _date_range(name: str, start: str | None, end: str | None) -> str | None:
    bounds = []
    for label, value in (("from", start), ("to", end)):
        text = str(value or "").strip()
        if text and not _DATE_RE.fullmatch(text):
            raise RedmineMcpError(f"{name}_{label} must be YYYY-MM-DD.", code="INVALID_ARGUMENT")
        bounds.append(text)
    if bounds[0] and bounds[1]:
        return f"><{bounds[0]}|{bounds[1]}"
    if bounds[0]:
        return f">={bounds[0]}"
    if bounds[1]:
        return f"<={bounds[1]}"
    return None


def _custom_field_values(issue: dict[str, Any], field_id: str) -> list[str] | None:
    for field in issue.get("custom_fields") if isinstance(issue.get("custom_fields"), list) else []:
        if isinstance(field, dict) and str(field.get("id")) == field_id:
            value = field.get("value")
            return [str(item or "").strip() for item in (value if isinstance(value, list) else [value])]
    return None


def _matches_custom_field_filter(issue: dict[str, Any], field_filter: dict[str, str]) -> bool:
    values = _custom_field_values(issue, field_filter["id"])
    if values is None:
        return False
    expected = field_filter["value"].casefold()
    if field_filter["match"] == "contains":
        return any(expected in value.casefold() for value in values)
    return any(expected == value.casefold() for value in values)


def _custom_field_summary(issue: dict[str, Any], field_ids: list[str]) -> list[dict[str, Any]]:
    fields = issue.get("custom_fields") if isinstance(issue.get("custom_fields"), list) else []
    return [
        {"id": field.get("id"), "name": str(field.get("name") or "").strip(), "value": field.get("value")}
        for field in fields
        if isinstance(field, dict) and str(field.get("id")) in field_ids
    ]


def redmine_search_issues(
    *,
    operation_id: str,
    environment: str,
    project_id: str | None = None,
    status_id: str = "open",
    tracker_id: str | None = None,
    subject_contains: str | None = None,
    custom_field_filters: Any = None,
    include_custom_fields: Any = None,
    author_id: str | None = None,
    assigned_to_id: str | None = None,
    category_id: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    closed_from: str | None = None,
    closed_to: str | None = None,
    limit: int = 100,
    offset: int = 0,
    env_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        project = _require_text("project_id", project_id or settings.project_id)
        field_filters = _custom_field_filters(custom_field_filters)
        if include_custom_fields not in (None, "") and not isinstance(include_custom_fields, list):
            raise RedmineMcpError("include_custom_fields must be an array.", code="INVALID_ARGUMENT")
        included_fields = [
            _custom_field_id(field_id, f"include_custom_fields[{index}]")
            for index, field_id in enumerate(include_custom_fields or [])
        ]
        page_offset = max(int(offset), 0)
        query: dict[str, Any] = {
            "project_id": project,
            "status_id": status_id,
            "tracker_id": tracker_id,
            "author_id": author_id,
            "assigned_to_id": assigned_to_id,
            "category_id": category_id,
            "subject": f"~{subject_contains.strip()}" if str(subject_contains or "").strip() else None,
            "updated_on": _date_range("updated", updated_from, updated_to),
            "closed_on": _date_range("closed", closed_from, closed_to),
            "limit": min(max(int(limit), 1), 100),
            "offset": page_offset,
            "sort": "updated_on:desc",
        }
        for field_filter in field_filters:
            prefix = "~" if field_filter["match"] == "contains" else ""
            query[f"cf_{field_filter['id']}"] = prefix + field_filter["value"]
        response = client.search_issues(query)
        issues = [issue for issue in response["issues"] if issue.get("id") not in (None, "")]
        # Redmine silently drops filters on fields that are not filterable or visible.
        ignored = sorted(
            {
                field_filter["id"]
                for issue in issues
                for field_filter in field_filters
                if not _matches_custom_field_filter(issue, field_filter)
            }
        )
        if ignored:
            raise RedmineMcpError(
                "Redmine did not apply the custom field filter for field ID(s) "
                f"{', '.join(ignored)}; results were discarded instead of returned unfiltered.",
                code="FILTER_NOT_APPLIED",
            )
        safe_issues = []
        for issue in issues:
            row: dict[str, Any] = {
                "id": str(issue.get("id") or ""),
                "subject": str(issue.get("subject") or ""),
                "status": str((issue.get("status") or {}).get("name") or ""),
                "updated_on": issue.get("updated_on"),
                "url": client.issue_url(str(issue.get("id") or "")),
            }
            if included_fields:
                row["custom_fields"] = _custom_field_summary(issue, included_fields)
            safe_issues.append(row)
        safe_issues, redactions = _redacted(safe_issues)
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "project_id": project,
                "total_count": response["total_count"],
                "offset": page_offset,
                "issue_count": len(safe_issues),
                "issues": safe_issues,
                "redactions": redactions,
            }
        )
    except Exception as exc:
        return _failure(operation_id, "search", exc)


def _named_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {"id": value.get("id"), "name": str(value.get("name") or "").strip()}


def _safe_issue_detail(
    issue: dict[str, Any],
    client: RedmineClient,
    status_names: dict[str, str],
) -> dict[str, Any]:
    custom_fields = []
    for field in issue.get("custom_fields") if isinstance(issue.get("custom_fields"), list) else []:
        if not isinstance(field, dict):
            continue
        custom_fields.append(
            {
                "id": field.get("id"),
                "name": str(field.get("name") or "").strip(),
                "value": field.get("value"),
            }
        )
    field_names = {str(field["id"]): field["name"] for field in custom_fields}
    journals = []
    for journal in issue.get("journals") if isinstance(issue.get("journals"), list) else []:
        if not isinstance(journal, dict):
            continue
        details = []
        for detail in journal.get("details") if isinstance(journal.get("details"), list) else []:
            if not isinstance(detail, dict):
                continue
            prop = str(detail.get("property") or "")
            name = str(detail.get("name") or "")
            is_status = prop == "attr" and name == "status_id"
            details.append(
                {
                    "property": prop,
                    "name": name,
                    "field_name": field_names.get(name) if prop == "cf" else None,
                    "old_value": detail.get("old_value"),
                    "new_value": detail.get("new_value"),
                    "old_label": status_names.get(str(detail.get("old_value"))) if is_status else None,
                    "new_label": status_names.get(str(detail.get("new_value"))) if is_status else None,
                }
            )
        journals.append(
            {
                "id": journal.get("id"),
                "user": _named_ref(journal.get("user")),
                "notes": str(journal.get("notes") or ""),
                "private": bool(journal.get("private_notes")),
                "created_on": journal.get("created_on"),
                "details": details,
            }
        )
    attachments = []
    for attachment in issue.get("attachments") if isinstance(issue.get("attachments"), list) else []:
        if not isinstance(attachment, dict):
            continue
        attachments.append(
            {
                "id": attachment.get("id"),
                "filename": str(attachment.get("filename") or ""),
                "filesize": attachment.get("filesize"),
                "content_type": str(attachment.get("content_type") or ""),
                "created_on": attachment.get("created_on"),
            }
        )
    issue_id = str(issue.get("id") or "")
    return {
        "id": issue_id,
        "url": client.issue_url(issue_id),
        "subject": str(issue.get("subject") or ""),
        "description": str(issue.get("description") or ""),
        "status": _named_ref(issue.get("status")),
        "tracker": _named_ref(issue.get("tracker")),
        "priority": _named_ref(issue.get("priority")),
        "project": _named_ref(issue.get("project")),
        "author": _named_ref(issue.get("author")),
        "assigned_to": _named_ref(issue.get("assigned_to")),
        "fixed_version": _named_ref(issue.get("fixed_version")),
        "category": _named_ref(issue.get("category")),
        "done_ratio": issue.get("done_ratio"),
        "start_date": issue.get("start_date"),
        "due_date": issue.get("due_date"),
        "created_on": issue.get("created_on"),
        "updated_on": issue.get("updated_on"),
        "closed_on": issue.get("closed_on"),
        "custom_fields": custom_fields,
        "journals": journals,
        "journal_count": len(journals),
        "attachments": attachments,
        "attachment_count": len(attachments),
    }


def redmine_get_issue(
    *,
    operation_id: str,
    environment: str,
    issue_id: str,
    env_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        selected_issue_id = _require_text("issue_id", issue_id)
        issue = client.get_issue(selected_issue_id, include="journals,attachments")
        status_names = {
            str(status.get("id")): str(status.get("name") or "")
            for status in client.get_issue_statuses()
        }
        detail, redactions = _redacted(_safe_issue_detail(issue, client, status_names))
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "issue": detail,
                "redactions": redactions,
            }
        )
    except Exception as exc:
        return _failure(operation_id, "get-issue", exc)


def redmine_list_projects(
    *,
    operation_id: str,
    environment: str,
    query: str | None = None,
    env_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        listing = client.list_projects()
        needle = str(query or "").strip().casefold()
        safe_projects = [
            {
                "id": project.get("id"),
                "identifier": str(project.get("identifier") or ""),
                "name": str(project.get("name") or ""),
                "status": project.get("status"),
            }
            for project in listing["projects"]
            if project.get("id") not in (None, "")
            and (
                not needle
                or needle in str(project.get("identifier") or "").casefold()
                or needle in str(project.get("name") or "").casefold()
            )
        ]
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "total_count": listing["total_count"],
                "fetched_count": len(listing["projects"]),
                "truncated": len(listing["projects"]) < listing["total_count"],
                "project_count": len(safe_projects),
                "projects": safe_projects,
            }
        )
    except Exception as exc:
        return _failure(operation_id, "list-projects", exc)


def redmine_get_project_metadata(
    *,
    operation_id: str,
    environment: str,
    project_id: str | None = None,
    env_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        project = _require_text("project_id", project_id or settings.project_id)
        metadata = client.get_project_metadata(project)

        def summaries(values: Any, *, include_closed: bool = False) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for value in values if isinstance(values, list) else []:
                if not isinstance(value, dict):
                    continue
                row: dict[str, Any] = {
                    "id": value.get("id"),
                    "name": str(value.get("name") or ""),
                }
                if include_closed:
                    row["is_closed"] = bool(value.get("is_closed"))
                rows.append(row)
            return rows

        project_data = metadata.get("project") if isinstance(metadata.get("project"), dict) else {}
        custom_fields = []
        for field in metadata.get("custom_fields") if isinstance(metadata.get("custom_fields"), list) else []:
            if not isinstance(field, dict):
                continue
            custom_fields.append(
                {
                    "id": field.get("id"),
                    "name": str(field.get("name") or ""),
                    "field_format": str(field.get("field_format") or ""),
                    "is_required": bool(field.get("is_required")),
                    "multiple": bool(field.get("multiple")),
                }
            )
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "project": {
                    "id": project_data.get("id"),
                    "identifier": str(project_data.get("identifier") or project),
                    "name": str(project_data.get("name") or ""),
                },
                "trackers": summaries(metadata.get("trackers")),
                "priorities": summaries(metadata.get("priorities")),
                "custom_fields": custom_fields,
                "statuses": summaries(metadata.get("statuses"), include_closed=True),
            }
        )
    except Exception as exc:
        return _failure(operation_id, "project-metadata", exc)


def redmine_validate_template(
    *,
    operation_id: str,
    environment: str,
    template_file: str,
    env_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    try:
        settings, _client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        summary = validate_template(load_template(template_file))
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "valid": not summary["blocked_fields"],
                **summary,
            }
        )
    except Exception as exc:
        return _failure(operation_id, "template-validation", exc)


def _redmine_bug(
    *,
    operation_id: str,
    environment: str,
    subject: str,
    description: str,
    tracker_id: str | None = None,
    priority_id: str | None = None,
    severity: str | None = None,
    custom_priority: Any = None,
    dedupe_marker: str,
    attachments: Any = None,
    project_id: str | None = None,
    custom_fields: Any = None,
    category_id: str | None = None,
    assigned_to_id: str | None = None,
    fixed_version_id: str | None = None,
    template_file: str | None = None,
    dedupe: str = "open",
    write: bool = False,
    preview_digest: str | None = None,
    env_file: str | None = None,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    attempt_audit_id: str | None = None
    attachment_metadata: list[dict[str, Any]] = []
    planned_redmine_action: str | None = None
    affected_issue: RedmineIssue | None = None
    field_verification: dict[str, Any] | None = None
    selected_text_format: dict[str, Any] | None = None
    format_validation: dict[str, Any] | None = None
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        selected_template_file = template_file or settings.template_file or None
        merged = merge_template_values(
            load_template(selected_template_file),
            project_id=project_id or settings.project_id,
            tracker_id=tracker_id,
            priority_id=priority_id,
            severity=severity,
            custom_priority=custom_priority,
            custom_fields=custom_fields,
            category_id=category_id,
            assigned_to_id=assigned_to_id,
            fixed_version_id=fixed_version_id,
        )
        payload = _build_issue_payload(
            project_id=merged["project_id"],
            subject=subject,
            description=description,
            tracker_id=merged["tracker_id"],
            priority_id=merged["priority_id"],
            custom_fields=merged["custom_fields"],
            category_id=merged["category_id"],
            assigned_to_id=merged["assigned_to_id"],
            fixed_version_id=merged["fixed_version_id"],
        )
        selected_text_format = merged["text_format"]
        format_validation = validate_redmine_text(
            str(payload["description"]),
            selected_text_format,
            environment=selected,
            field="description",
        )
        prepared_attachments: list[PreparedAttachment] = prepare_attachments(attachments)
        attachment_metadata = [attachment.metadata() for attachment in prepared_attachments]

        def build_plan() -> dict[str, Any]:
            return _build_bug_plan(
                client=client,
                operation_id=operation_id,
                environment=selected,
                issue_payload=payload,
                issue_fields=merged["issue_fields"],
                text_format=selected_text_format,
                format_validation=format_validation,
                attachments=attachment_metadata,
                dedupe_marker=dedupe_marker,
                dedupe=dedupe,
            )

        if not write:
            return _success(_bug_preview(build_plan()))

        with _dedupe_lock(dedupe_marker):
            plan = build_plan()
            planned_redmine_action = str(plan["action"])
            validate_preview_digest(plan, str(preview_digest or ""))
            if plan["action"] == "blocked":
                raise RedmineMcpError(
                    "; ".join(plan["warnings"]) or "Redmine bug write is blocked by policy.",
                    code="WRITE_BLOCKED",
                )
            started_audit = write_operation_audit(
                {
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "environment": selected,
                    "action": "create-bug",
                    "status": "started",
                    "preview_digest": preview_digest,
                    "dedupe_digest": plan["dedupe_digest"],
                    "planned_redmine_action": plan["action"],
                    "text_format": plan["text_format"],
                    "format_validation": _format_validation_audit(plan["format_validation"]),
                    "attachment_action": plan["attachment_action"],
                    "attachment_count": len(attachment_metadata),
                    "attachments": attachment_metadata,
                    "started_at": utc_now_iso(),
                },
                audit_dir,
            )
            attempt_audit_id = started_audit.name
            if plan["action"] == "reuse":
                existing = plan["existing_issue"]
                issue = RedmineIssue(
                    id=str(existing["id"]),
                    url=str(existing["url"]),
                    subject=str(existing["subject"]),
                    state="open",
                    reused=True,
                )
                action = "reused"
                attachment_status = "not-uploaded-reused" if prepared_attachments else "none"
            else:
                upload_references = []
                for attachment in prepared_attachments:
                    upload_token = client.upload_attachment(
                        filename=attachment.filename,
                        content=attachment.content,
                    )
                    upload_references.append(attachment.upload_reference(upload_token))
                write_payload = {**payload}
                if upload_references:
                    write_payload["uploads"] = upload_references
                issue = client.create_issue(write_payload)
                affected_issue = issue
                action = "created"
                attachment_status = "attached" if prepared_attachments else "none"
                field_verification = _verify_issue_fields(
                    client.get_issue(issue.id),
                    plan["issue_fields"],
                )
                if not field_verification["verified"]:
                    raise RedmineMcpError(
                        "Created Redmine issue fields do not match the confirmed preview.",
                        code="VERIFICATION_FAILED",
                    )
            if action == "reused":
                field_verification = {
                    "status": "not-required-reused",
                    "verified": None,
                    "severity": None,
                    "priority": None,
                }
            audit_path = write_operation_audit(
                {
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "environment": selected,
                    "action": "create-bug",
                    "status": "success",
                    "redmine_action": action,
                    "preview_digest": preview_digest,
                    "dedupe_digest": plan["dedupe_digest"],
                    "issue": _issue_result(issue),
                    "field_verification": field_verification,
                    "text_format": plan["text_format"],
                    "format_validation": _format_validation_audit(plan["format_validation"]),
                    "attachment_status": attachment_status,
                    "attachment_count": len(attachment_metadata),
                    "attachments": attachment_metadata,
                    "finished_at": utc_now_iso(),
                },
                audit_dir,
                audit_id=attempt_audit_id,
            )
            return _success(
                {
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "environment": selected,
                    "preview_digest": str(preview_digest),
                    "status": "success",
                    "action": action,
                    "issue": _issue_result(issue),
                    "field_verification": field_verification,
                    "attachment_status": attachment_status,
                    "attachment_count": len(attachment_metadata),
                    "comment_status": "not-required",
                    "audit_id": audit_path.name,
                }
            )
    except Exception as exc:
        if write:
            audit_id = _attempt_failure_audit(
                operation_id=operation_id,
                environment=environment,
                action="create-bug",
                preview_digest=preview_digest,
                error=exc,
                audit_dir=audit_dir,
                audit_id=attempt_audit_id,
                details={
                    "planned_redmine_action": planned_redmine_action,
                    "attachment_count": len(attachment_metadata),
                    "attachments": attachment_metadata,
                    "issue": _issue_result(affected_issue),
                    "field_verification": field_verification,
                    "text_format": selected_text_format,
                    "format_validation": _format_validation_audit(format_validation),
                },
            )
            if audit_id is None:
                exc = RedmineMcpError(
                    f"{exc}; additionally, the required Redmine audit record could not be written.",
                    code="AUDIT_WRITE_FAILED",
                )
        stage = "write" if write else "preview"
        failure = _failure(operation_id, f"redmine-bug-{stage}", exc)
        if affected_issue is not None:
            failure["error"]["partial_result"] = redact_secrets(
                {
                    "action": "created",
                    "issue": _issue_result(affected_issue),
                    "field_verification": field_verification,
                    "audit_id": audit_id,
                }
            )
        return failure


def redmine_preview_bug(**kwargs: Any) -> dict[str, Any]:
    return _redmine_bug(**{**kwargs, "write": False, "preview_digest": None})


def redmine_create_bug(**kwargs: Any) -> dict[str, Any]:
    return _redmine_bug(**kwargs)


def _redmine_comment(
    *,
    operation_id: str,
    environment: str,
    issue_id: str,
    notes: str,
    attachments: Any = None,
    template_file: str | None = None,
    write: bool = False,
    preview_digest: str | None = None,
    env_file: str | None = None,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    attempt_audit_id: str | None = None
    text_format: dict[str, Any] | None = None
    format_validation: dict[str, Any] | None = None
    attachment_metadata: list[dict[str, Any]] = []
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        selected_issue_id = _require_text("issue_id", issue_id)
        selected_notes = _require_text("notes", notes)
        selected_template_file = template_file or settings.template_file or None
        selected_template = load_template(selected_template_file)
        template_summary = validate_template(selected_template) if selected_template else {"text_format": None}
        text_format = template_summary.get("text_format")
        format_validation = validate_redmine_text(
            selected_notes,
            text_format,
            environment=selected,
            field="notes",
        )
        warnings = [
            str(item.get("message") or "Redmine text format warning.")
            for item in format_validation["warnings"]
        ]
        if not format_validation["valid"]:
            warnings.append("Redmine comment format validation failed.")
        prepared_attachments: list[PreparedAttachment] = prepare_attachments(attachments)
        attachment_metadata = [attachment.metadata() for attachment in prepared_attachments]
        warnings.extend(attachment_warnings(attachment_metadata))
        plan = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": _require_text("operation_id", operation_id, max_length=128),
            "environment": selected,
            "action": "add-comment" if format_validation["valid"] else "blocked",
            "planned_write": bool(format_validation["valid"]),
            "issue_id": selected_issue_id,
            "notes": selected_notes,
            "text_format": text_format,
            "format_validation": format_validation,
            "attachments": attachment_metadata,
            "warnings": warnings,
        }
        assert_safe_contract(plan)
        digest = payload_digest(plan)
        if not write:
            return _success(
                {
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "environment": selected,
                    "mode": "preview",
                    "preview_digest": digest,
                    "planned_write": plan["planned_write"],
                    "action": plan["action"],
                    "issue_id": selected_issue_id,
                    "notes_digest": hashlib.sha256(selected_notes.encode("utf-8")).hexdigest(),
                    "text_format": text_format,
                    "format_validation": format_validation,
                    "attachment_action": "upload-and-attach" if attachment_metadata else "none",
                    "attachment_count": len(attachment_metadata),
                    "attachments": attachment_metadata,
                    "warnings": warnings,
                }
            )
        validate_preview_digest(plan, str(preview_digest or ""))
        if not plan["planned_write"]:
            raise RedmineMcpError(
                "Redmine comment write is blocked by text format validation.",
                code="WRITE_BLOCKED",
            )
        notes_digest = hashlib.sha256(selected_notes.encode("utf-8")).hexdigest()
        for previous_path, previous in find_operation_audits(operation_id, "add-comment", audit_dir):
            if previous.get("preview_digest") != preview_digest:
                continue
            if previous.get("status") == "added":
                return _success(
                    {
                        "schema_version": CONTRACT_SCHEMA_VERSION,
                        "operation_id": operation_id,
                        "environment": selected,
                        "preview_digest": str(preview_digest),
                        "status": "skipped-resume",
                        "issue_id": selected_issue_id,
                        "audit_id": previous_path.name,
                    }
                )
            # A "failed" attempt may still have reached Redmine (for example a timeout after
            # the PUT was applied), so it is checked against the journals like "started".
            if previous.get("status") in {"started", "failed"}:
                matching_journal = next(
                    (
                        journal
                        for journal in client.get_issue_journals(selected_issue_id)
                        if hashlib.sha256(str(journal.get("notes") or "").encode("utf-8")).hexdigest()
                        == notes_digest
                    ),
                    None,
                )
                if matching_journal is None and previous.get("status") == "failed":
                    continue
                if matching_journal is None:
                    raise RedmineMcpError(
                        "A previous Redmine comment operation is indeterminate; refusing to add a possible duplicate comment.",
                        code="INDETERMINATE_OPERATION",
                    )
                recovered = {
                    **previous,
                    "status": "added",
                    "recovered": True,
                    "journal_id": matching_journal.get("id"),
                    "finished_at": utc_now_iso(),
                }
                write_operation_audit(recovered, audit_dir, audit_id=previous_path.name)
                return _success(
                    {
                        "schema_version": CONTRACT_SCHEMA_VERSION,
                        "operation_id": operation_id,
                        "environment": selected,
                        "preview_digest": str(preview_digest),
                        "status": "skipped-resume",
                        "issue_id": selected_issue_id,
                        "audit_id": previous_path.name,
                    }
                )
        started_audit = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": "add-comment",
                "status": "started",
                "issue_id": selected_issue_id,
                "notes_digest": notes_digest,
                "preview_digest": preview_digest,
                "text_format": text_format,
                "format_validation": _format_validation_audit(format_validation),
                "attachment_count": len(attachment_metadata),
                "attachments": attachment_metadata,
                "started_at": utc_now_iso(),
            },
            audit_dir,
        )
        attempt_audit_id = started_audit.name
        # Upload tokens only travel to Redmine; results and audits keep metadata.
        upload_references = [
            attachment.upload_reference(
                client.upload_attachment(filename=attachment.filename, content=attachment.content)
            )
            for attachment in prepared_attachments
        ]
        if upload_references:
            client.add_comment(selected_issue_id, selected_notes, uploads=upload_references)
        else:
            client.add_comment(selected_issue_id, selected_notes)
        attachment_status = "attached" if attachment_metadata else "none"
        audit_path = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": "add-comment",
                "status": "added",
                "issue_id": selected_issue_id,
                "notes_digest": notes_digest,
                "preview_digest": preview_digest,
                "text_format": text_format,
                "format_validation": _format_validation_audit(format_validation),
                "attachment_status": attachment_status,
                "attachment_count": len(attachment_metadata),
                "attachments": attachment_metadata,
                "finished_at": utc_now_iso(),
            },
            audit_dir,
            audit_id=attempt_audit_id,
        )
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "preview_digest": str(preview_digest),
                "status": "added",
                "issue_id": selected_issue_id,
                "attachment_status": attachment_status,
                "attachment_count": len(attachment_metadata),
                "audit_id": audit_path.name,
            }
        )
    except Exception as exc:
        if write:
            audit_id = _attempt_failure_audit(
                operation_id=operation_id,
                environment=environment,
                action="add-comment",
                preview_digest=preview_digest,
                error=exc,
                audit_dir=audit_dir,
                audit_id=attempt_audit_id,
                details={
                    "attachment_count": len(attachment_metadata),
                    "attachments": attachment_metadata,
                    "text_format": text_format,
                    "format_validation": _format_validation_audit(format_validation),
                },
            )
            if audit_id is None:
                exc = RedmineMcpError(
                    f"{exc}; additionally, the required Redmine audit record could not be written.",
                    code="AUDIT_WRITE_FAILED",
                )
        stage = "write" if write else "preview"
        return _failure(operation_id, f"redmine-comment-{stage}", exc)


def redmine_preview_comment(**kwargs: Any) -> dict[str, Any]:
    return _redmine_comment(**{**kwargs, "write": False, "preview_digest": None})


def redmine_add_comment(**kwargs: Any) -> dict[str, Any]:
    return _redmine_comment(**kwargs)


SUBJECT_MAX_LENGTH = 255
_SUBJECT_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DESCRIPTION_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Reference fields a single-field PUT must leave alone; the readback reports any that moved.
_UPDATE_GUARDED_REFS = (
    "project",
    "tracker",
    "status",
    "priority",
    "assigned_to",
    "fixed_version",
    "category",
)
# Only these issue fields can be changed on an existing issue, and only one per call.
_UPDATABLE_TEXT_FIELDS = ("subject", "description")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_text_field(value: str) -> str:
    # Redmine stores descriptions with CRLF line endings; compare and send LF text.
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _same_text(left: str, right: str) -> bool:
    return _normalize_text_field(left).strip() == _normalize_text_field(right).strip()


def _validate_new_subject(value: Any) -> str:
    raw = str(value if value is not None else "")
    if "\n" in raw or "\r" in raw:
        raise RedmineMcpError("subject must be a single line (no line breaks).", code="INVALID_ARGUMENT")
    if _SUBJECT_CONTROL_RE.search(raw):
        raise RedmineMcpError("subject must not contain control characters.", code="INVALID_ARGUMENT")
    return _require_text("subject", raw, max_length=SUBJECT_MAX_LENGTH)


def _validate_new_description(value: Any) -> str:
    raw = _normalize_text_field(str(value if value is not None else ""))
    if _DESCRIPTION_CONTROL_RE.search(raw):
        raise RedmineMcpError("description must not contain control characters.", code="INVALID_ARGUMENT")
    return _require_text("description", raw)


def _guarded_snapshot(issue: dict[str, Any], updated_field: str) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for name in _UPDATE_GUARDED_REFS:
        value = issue.get(name)
        snapshot[name] = str(value.get("id") or "") if isinstance(value, dict) else ""
    for name in _UPDATABLE_TEXT_FIELDS:
        if name != updated_field:
            snapshot[name] = _normalize_text_field(str(issue.get(name) or "")).strip()
    return snapshot


def _text_diff(current: str, new: str) -> str:
    lines = difflib.unified_diff(
        _normalize_text_field(current).splitlines(),
        _normalize_text_field(new).splitlines(),
        fromfile="current",
        tofile="new",
        lineterm="",
    )
    return "\n".join(lines)


def _update_issue_text_field(
    *,
    field: str,
    operation_id: str,
    environment: str,
    issue_id: str,
    value: Any,
    expected_current: str | None = None,
    template_file: str | None = None,
    write: bool = False,
    preview_digest: str | None = None,
    env_file: str | None = None,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Preview, then change one text field of an existing issue after a confirmed digest.

    The digest binds the issue ID, the field value read at preview time, and the new value, so a
    value changed by someone else after preview rejects the write instead of overwriting it.
    The PUT body carries only that field.
    """
    if field not in _UPDATABLE_TEXT_FIELDS:
        raise ValueError(f"Unsupported issue field: {field}")
    action_name = f"update-{field}"
    attempt_audit_id: str | None = None
    selected_issue_id = ""
    current_value: str | None = None
    new_value: str | None = None
    field_written = False
    verification: dict[str, Any] | None = None
    format_validation: dict[str, Any] | None = None
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        selected_issue_id = _require_text("issue_id", issue_id)
        new_value = _validate_new_subject(value) if field == "subject" else _validate_new_description(value)
        text_format: dict[str, Any] | None = None
        if field == "description":
            selected_template = load_template(template_file or settings.template_file or None)
            text_format = (validate_template(selected_template) if selected_template else {}).get("text_format")
            format_validation = validate_redmine_text(
                new_value, text_format, environment=selected, field="description"
            )
        before = client.get_issue(selected_issue_id)
        current_value = str(before.get(field) or "")
        issue_url = client.issue_url(selected_issue_id)

        if write:
            # Resume: an earlier attempt with this digest already reached Redmine.
            for previous_path, previous in find_operation_audits(operation_id, action_name, audit_dir):
                if (
                    previous.get("preview_digest") != preview_digest
                    or previous.get("issue_id") != selected_issue_id
                    or previous.get(f"new_{field}_sha256") != _sha256(new_value)
                    or not _same_text(current_value, new_value)
                ):
                    continue
                if previous.get("status") != "updated":
                    write_operation_audit(
                        {**previous, "status": "updated", "recovered": True, "finished_at": utc_now_iso()},
                        audit_dir,
                        audit_id=previous_path.name,
                    )
                return _success(
                    {
                        "schema_version": CONTRACT_SCHEMA_VERSION,
                        "operation_id": operation_id,
                        "environment": selected,
                        "preview_digest": str(preview_digest),
                        "status": "skipped-resume",
                        "issue_id": selected_issue_id,
                        "issue_url": issue_url,
                        "audit_id": previous_path.name,
                    }
                )

        expected = None if expected_current in (None, "") else str(expected_current)
        if expected is not None and expected != current_value:
            raise RedmineMcpError(
                f"Issue {selected_issue_id} {field} is now {current_value!r}, not the expected "
                f"{expected!r}; it was changed by someone else. Review it and run a new preview.",
                code=f"{field.upper()}_CHANGED",
            )
        warnings: list[str] = []
        if format_validation is not None:
            warnings.extend(
                str(item.get("message") or "Redmine text format warning.")
                for item in format_validation["warnings"]
            )
        if _same_text(current_value, new_value):
            action = "no-change"
            warnings.append(f"The new {field} equals the current {field}; nothing to write.")
        elif format_validation is not None and not format_validation["valid"]:
            action = "blocked"
            warnings.append("Redmine description format validation failed.")
        else:
            action = action_name
        plan = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": _require_text("operation_id", operation_id, max_length=128),
            "environment": selected,
            "action": action,
            "planned_write": action == action_name,
            "issue_id": selected_issue_id,
            f"current_{field}": current_value,
            f"new_{field}": new_value,
            "write_fields": [field],
            "text_format": text_format,
            "format_validation": format_validation,
            "warnings": warnings,
        }
        assert_safe_contract(plan)
        change: dict[str, Any] = {"from": current_value, "to": new_value}
        if field == "description":
            change["diff"] = _text_diff(current_value, new_value)
        if not write:
            preview = {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "mode": "preview",
                "preview_digest": payload_digest(plan),
                "planned_write": plan["planned_write"],
                "action": action,
                "issue_id": selected_issue_id,
                "issue_url": issue_url,
                f"{field}_change": change,
                "write_fields": plan["write_fields"],
                "warnings": warnings,
            }
            if format_validation is not None:
                preview["text_format"] = text_format
                preview["format_validation"] = format_validation
            return _success(preview)
        try:
            validate_preview_digest(plan, str(preview_digest or ""))
        except ContractError as exc:
            current_hint = (
                repr(current_value) if field == "subject" else f"sha256 {_sha256(current_value)[:16]}"
            )
            raise RedmineMcpError(
                f"{exc} The current {field} of issue {selected_issue_id} is {current_hint}; "
                "if it changed after preview, review it and run a new preview.",
                code="PREVIEW_DIGEST_MISMATCH",
            ) from exc
        if not plan["planned_write"]:
            raise RedmineMcpError(
                "; ".join(warnings) or f"Redmine {field} write is blocked.",
                code="WRITE_BLOCKED",
            )
        # The previous value stays in the local audit so a wrong edit can be restored by hand.
        audit_values = {
            f"previous_{field}": current_value,
            f"new_{field}": new_value,
            f"previous_{field}_sha256": _sha256(current_value),
            f"new_{field}_sha256": _sha256(new_value),
        }
        started_audit = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": action_name,
                "status": "started",
                "issue_id": selected_issue_id,
                **audit_values,
                "preview_digest": preview_digest,
                "format_validation": _format_validation_audit(format_validation),
                "started_at": utc_now_iso(),
            },
            audit_dir,
        )
        attempt_audit_id = started_audit.name
        client.update_issue_field(selected_issue_id, field, new_value)
        field_written = True
        after = client.get_issue(selected_issue_id)
        actual_value = str(after.get(field) or "")
        before_snapshot = _guarded_snapshot(before, field)
        after_snapshot = _guarded_snapshot(after, field)
        changed_fields = [name for name in before_snapshot if before_snapshot[name] != after_snapshot[name]]
        verified = _same_text(actual_value, new_value)
        verification = {
            "status": "verified" if verified else "verification_failed",
            "verified": verified,
            f"expected_{field}_sha256": _sha256(new_value),
            f"actual_{field}_sha256": _sha256(_normalize_text_field(actual_value).strip()),
            "other_fields_unchanged": not changed_fields,
            "changed_fields": changed_fields,
        }
        if field == "subject":
            verification["expected_subject"] = new_value
            verification["actual_subject"] = actual_value
        if not verified:
            raise RedmineMcpError(
                f"Redmine issue {field} readback does not match the confirmed preview.",
                code="VERIFICATION_FAILED",
            )
        if changed_fields:
            warnings.append(
                "These fields differ between the pre-write read and the readback although only the "
                f"{field} was sent: {', '.join(changed_fields)}. Check the issue history."
            )
        audit_path = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": action_name,
                "status": "updated",
                "issue_id": selected_issue_id,
                **audit_values,
                "preview_digest": preview_digest,
                "format_validation": _format_validation_audit(format_validation),
                "verification": verification,
                "finished_at": utc_now_iso(),
            },
            audit_dir,
            audit_id=attempt_audit_id,
        )
        result = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": operation_id,
            "environment": selected,
            "preview_digest": str(preview_digest),
            "status": "updated",
            "issue_id": selected_issue_id,
            "issue_url": issue_url,
            "verification": verification,
            "warnings": warnings,
            "audit_id": audit_path.name,
        }
        if field == "subject":
            result["subject_change"] = change
        return _success(result)
    except Exception as exc:
        audit_id = None
        if write:
            audit_id = _attempt_failure_audit(
                operation_id=operation_id,
                environment=environment,
                action=action_name,
                preview_digest=preview_digest,
                error=exc,
                audit_dir=audit_dir,
                audit_id=attempt_audit_id,
                details={
                    "issue_id": selected_issue_id,
                    f"previous_{field}": current_value,
                    f"new_{field}": new_value,
                    f"new_{field}_sha256": _sha256(new_value) if new_value is not None else None,
                    f"{field}_written": field_written,
                    "format_validation": _format_validation_audit(format_validation),
                    "verification": verification,
                },
            )
            if audit_id is None:
                exc = RedmineMcpError(
                    f"{exc}; additionally, the required Redmine audit record could not be written.",
                    code="AUDIT_WRITE_FAILED",
                )
        stage = "write" if write else "preview"
        failure = _failure(operation_id, f"redmine-{field}-{stage}", exc)
        if field_written:
            failure["error"]["partial_result"] = redact_secrets(
                {
                    "action": f"{field}-sent",
                    "issue_id": selected_issue_id,
                    "verification": verification,
                    "audit_id": audit_id,
                }
            )
        return failure


def redmine_update_subject(
    *,
    subject: str,
    expected_current_subject: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Change only an existing issue's subject; see `_update_issue_text_field`."""
    return _update_issue_text_field(
        field="subject", value=subject, expected_current=expected_current_subject, **kwargs
    )


def redmine_update_description(*, description: str, **kwargs: Any) -> dict[str, Any]:
    """Change only an existing issue's description; see `_update_issue_text_field`."""
    return _update_issue_text_field(field="description", value=description, **kwargs)


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "redmine_health": redmine_health,
    "redmine_search_issues": redmine_search_issues,
    "redmine_get_issue": redmine_get_issue,
    "redmine_list_projects": redmine_list_projects,
    "redmine_get_project_metadata": redmine_get_project_metadata,
    "redmine_validate_template": redmine_validate_template,
    "redmine_preview_bug": redmine_preview_bug,
    "redmine_create_bug": redmine_create_bug,
    "redmine_preview_comment": redmine_preview_comment,
    "redmine_add_comment": redmine_add_comment,
    "redmine_update_subject": redmine_update_subject,
    "redmine_update_description": redmine_update_description,
}


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name not in TOOLS:
        return {
            "ok": False,
            "code": 1,
            "error": {
                "type": "UnknownTool",
                "message": f"Unknown tool: {name}",
            },
        }
    try:
        return TOOLS[name](**(arguments or {}))
    except TypeError as exc:
        operation_id = str((arguments or {}).get("operation_id") or "unknown-operation")
        return _failure(operation_id, "arguments", RedmineMcpError(str(exc), code="INVALID_ARGUMENT"))
