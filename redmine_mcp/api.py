from __future__ import annotations

import hashlib
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

from .attachments import PreparedImageAttachment, prepare_image_attachments
from .audit import find_operation_audits, utc_now_iso, write_operation_audit
from .client import RedmineClient
from .config import DEFAULT_AUDIT_DIR, DEFAULT_TIMEOUT_SECONDS, RedmineSettings, load_redmine_settings
from .errors import RedmineMcpError, normalize_error, redact_secrets
from .models import RedmineIssue
from .policy import blocked_manager_fields, manager_fields_allowed, validate_environment
from .templates import load_template, merge_template_values, validate_template


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
    if blocked_fields:
        action = "blocked"
        existing_issue = open_issue or closed_issue
        warnings.append("Manager-only Redmine fields are blocked in this environment.")
    elif open_issue is not None:
        action = "reuse"
        existing_issue = open_issue
        if attachments:
            warnings.append(
                "The dedupe marker matches an open issue; image attachments are not uploaded to reused issues."
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
    }
    if plan["existing_issue"] is not None:
        result["existing_issue"] = plan["existing_issue"]
    return result


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
    try:
        path = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": environment,
                "action": action,
                "status": "failed",
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


def redmine_search_issues(
    *,
    operation_id: str,
    environment: str,
    project_id: str | None = None,
    status_id: str = "open",
    tracker_id: str | None = None,
    limit: int = 100,
    env_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        project = _require_text("project_id", project_id or settings.project_id)
        issues = client.find_issues(
            project_id=project,
            status_id=status_id,
            tracker_id=tracker_id,
            limit=limit,
        )
        safe_issues = [
            {
                "id": str(issue.get("id") or ""),
                "subject": str(issue.get("subject") or ""),
                "status": str((issue.get("status") or {}).get("name") or ""),
                "url": client.issue_url(str(issue.get("id") or "")),
            }
            for issue in issues
            if issue.get("id") not in (None, "")
        ]
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "project_id": project,
                "issue_count": len(safe_issues),
                "issues": safe_issues,
            }
        )
    except Exception as exc:
        return _failure(operation_id, "search", exc)


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
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        merged = merge_template_values(
            load_template(template_file),
            project_id=project_id or settings.project_id,
            tracker_id=tracker_id,
            priority_id=priority_id,
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
        prepared_attachments: list[PreparedImageAttachment] = prepare_image_attachments(attachments)
        attachment_metadata = [attachment.metadata() for attachment in prepared_attachments]

        def build_plan() -> dict[str, Any]:
            return _build_bug_plan(
                client=client,
                operation_id=operation_id,
                environment=selected,
                issue_payload=payload,
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
                action = "created"
                attachment_status = "attached" if prepared_attachments else "none"
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
                },
            )
            if audit_id is None:
                exc = RedmineMcpError(
                    f"{exc}; additionally, the required Redmine audit record could not be written.",
                    code="AUDIT_WRITE_FAILED",
                )
        stage = "write" if write else "preview"
        return _failure(operation_id, f"redmine-bug-{stage}", exc)


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
    write: bool = False,
    preview_digest: str | None = None,
    env_file: str | None = None,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    attempt_audit_id: str | None = None
    try:
        settings, client = _runtime(env_file, timeout)
        selected = validate_environment(environment, settings.environment)
        selected_issue_id = _require_text("issue_id", issue_id)
        selected_notes = _require_text("notes", notes)
        plan = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": _require_text("operation_id", operation_id, max_length=128),
            "environment": selected,
            "action": "add-comment",
            "issue_id": selected_issue_id,
            "notes": selected_notes,
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
                    "planned_write": True,
                    "issue_id": selected_issue_id,
                    "notes_digest": hashlib.sha256(selected_notes.encode("utf-8")).hexdigest(),
                    "warnings": [],
                }
            )
        validate_preview_digest(plan, str(preview_digest or ""))
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
            if previous.get("status") == "started":
                matching_journal = next(
                    (
                        journal
                        for journal in client.get_issue_journals(selected_issue_id)
                        if hashlib.sha256(str(journal.get("notes") or "").encode("utf-8")).hexdigest()
                        == notes_digest
                    ),
                    None,
                )
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
                "started_at": utc_now_iso(),
            },
            audit_dir,
        )
        attempt_audit_id = started_audit.name
        client.add_comment(selected_issue_id, selected_notes)
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


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "redmine_health": redmine_health,
    "redmine_search_issues": redmine_search_issues,
    "redmine_get_project_metadata": redmine_get_project_metadata,
    "redmine_validate_template": redmine_validate_template,
    "redmine_preview_bug": redmine_preview_bug,
    "redmine_create_bug": redmine_create_bug,
    "redmine_preview_comment": redmine_preview_comment,
    "redmine_add_comment": redmine_add_comment,
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
        return _failure(operation_id, "arguments", exc)
