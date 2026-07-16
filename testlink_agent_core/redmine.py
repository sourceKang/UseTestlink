from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import DEFAULT_TIMEOUT_SECONDS
from .errors import RedmineError
from .models import ParsedResult, RedmineIssue
from .policy import (
    blocked_manager_fields,
    build_dedupe_key,
    dedupe_digest,
    dedupe_marker,
    redmine_manager_fields_allowed,
)


_TEMPLATE_TOKEN_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class RedmineClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        if not base_url:
            raise RedmineError("REDMINE_URL is required when --redmine-create-bugs is used.")
        if not api_key:
            raise RedmineError("REDMINE_API_KEY is required when --redmine-create-bugs is used.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def issue_url(self, issue_id: str | int) -> str:
        return f"{self.base_url}/issues/{issue_id}"

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query_items = {key: value for key, value in (query or {}).items() if value not in (None, "")}
        url = f"{self.base_url}{path}"
        if query_items:
            url = f"{url}?{urllib.parse.urlencode(query_items)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Redmine-API-Key": self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise RedmineError(f"Redmine HTTP {exc.code}: {response_body}") from exc
        except urllib.error.URLError as exc:
            raise RedmineError(f"Redmine connection failed: {exc.reason}") from exc

        if not response_body:
            return {}
        try:
            return json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RedmineError(f"Redmine returned non-JSON response: {response_body}") from exc

    def create_issue(self, issue_payload: dict[str, Any]) -> RedmineIssue:
        response = self.request_json("POST", "/issues.json", {"issue": issue_payload})
        issue = response.get("issue")
        if not isinstance(issue, dict) or "id" not in issue:
            raise RedmineError(f"Unexpected Redmine create issue response: {response}")
        issue_id = str(issue["id"])
        return RedmineIssue(
            id=issue_id,
            url=self.issue_url(issue_id),
            subject=str(issue.get("subject") or issue_payload.get("subject") or ""),
            reused=False,
        )

    def add_issue_comment(self, issue_id: str | int, notes: str) -> dict[str, Any]:
        if not str(issue_id).strip():
            raise RedmineError("Redmine issue ID is required before adding a comment.")
        if not notes.strip():
            raise RedmineError("Redmine comment notes must not be empty.")
        return self.request_json("PUT", f"/issues/{issue_id}.json", {"issue": {"notes": notes}})

    def find_open_issue_by_subject(
        self,
        project_id: str,
        subject: str,
        tracker_id: str | None = None,
    ) -> RedmineIssue | None:
        query = {
            "project_id": project_id,
            "status_id": "open",
            "tracker_id": tracker_id,
            "limit": 100,
            "sort": "updated_on:desc",
        }
        response = self.request_json("GET", "/issues.json", query=query)
        issues = response.get("issues") or []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if str(issue.get("subject") or "") == subject and "id" in issue:
                issue_id = str(issue["id"])
                return RedmineIssue(
                    id=issue_id,
                    url=self.issue_url(issue_id),
                    subject=subject,
                    reused=True,
                )
        return None

    def find_open_issue_by_dedupe_marker(
        self,
        project_id: str,
        marker: str,
        tracker_id: str | None = None,
    ) -> RedmineIssue | None:
        query = {
            "project_id": project_id,
            "status_id": "open",
            "tracker_id": tracker_id,
            "limit": 100,
            "sort": "updated_on:desc",
        }
        response = self.request_json("GET", "/issues.json", query=query)
        issues = response.get("issues") or []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            haystack = "\n".join(
                str(issue.get(field) or "")
                for field in ("subject", "description")
            )
            if marker in haystack and "id" in issue:
                issue_id = str(issue["id"])
                return RedmineIssue(
                    id=issue_id,
                    url=self.issue_url(issue_id),
                    subject=str(issue.get("subject") or ""),
                    reused=True,
                    dedupe_marker=marker,
                )
        return None

def truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."

def redmine_arg(args: argparse.Namespace, attr: str, env_name: str) -> str:
    return str(getattr(args, attr, None) or os.environ.get(env_name, "")).strip()

def redmine_optional_id(value: str | None) -> int | str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return text

def manager_fields_enabled() -> bool:
    return redmine_manager_fields_allowed()

def redmine_manager_fields(args: argparse.Namespace) -> dict[str, str]:
    return {
        "assigned_to_id": redmine_arg(args, "redmine_assigned_to_id", "REDMINE_ASSIGNED_TO_ID"),
        "fixed_version_id": redmine_arg(args, "redmine_fixed_version_id", "REDMINE_FIXED_VERSION_ID"),
    }

def reject_restricted_issue_fields(args: argparse.Namespace) -> None:
    blocked = blocked_manager_fields(redmine_manager_fields(args))
    if blocked:
        joined = ", ".join(blocked)
        raise RedmineError(
            f"Restricted Redmine fields are not allowed for automation-created bugs: {joined}. "
            "Set REDMINE_ALLOW_MANAGER_FIELDS=true only on a manager-owned machine to allow them."
        )

def load_redmine_template(args: argparse.Namespace) -> dict[str, Any]:
    template_path = redmine_arg(args, "redmine_template", "REDMINE_TEMPLATE")
    if not template_path:
        return {}
    path = Path(template_path)
    if not path.exists():
        raise RedmineError(f"Redmine template file does not exist: {path}")
    try:
        template = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RedmineError(f"Redmine template is not valid JSON: {path}: {exc}") from exc
    if not isinstance(template, dict):
        raise RedmineError(f"Redmine template must be a JSON object: {path}")
    return template

def _context_lookup(path: str, header: dict[str, str], result: ParsedResult, context: dict[str, Any]) -> Any:
    path = path.strip()
    if path == "today":
        return _datetime.date.today().isoformat()
    if path == "report_date":
        generated = header.get("Report generated on", "")
        match = re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", generated)
        if match:
            return match.group(0).replace("/", "-")
        return _datetime.date.today().isoformat()
    if path.startswith("env."):
        return os.environ.get(path[4:], "")
    if path.startswith("header."):
        return header.get(path[7:], "")
    if path.startswith("result."):
        return getattr(result, path[7:], "")
    if path.startswith("context."):
        value: Any = context
        for part in path[8:].split("."):
            if isinstance(value, dict):
                value = value.get(part, "")
            else:
                value = getattr(value, part, "")
            if value in (None, ""):
                return ""
        return value
    return ""

def render_redmine_template_value(
    value: Any,
    header: dict[str, str],
    result: ParsedResult,
    context: dict[str, Any],
) -> Any:
    if isinstance(value, str):
        return _TEMPLATE_TOKEN_RE.sub(lambda match: str(_context_lookup(match.group(1), header, result, context)), value)
    if isinstance(value, list):
        return [render_redmine_template_value(item, header, result, context) for item in value]
    if isinstance(value, dict):
        return {key: render_redmine_template_value(item, header, result, context) for key, item in value.items()}
    return value

def _is_blank_redmine_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value or all(_is_blank_redmine_value(item) for item in value)
    return False

def _custom_field_key(field: dict[str, Any]) -> str:
    field_id = field.get("id")
    if field_id not in (None, ""):
        return f"id:{field_id}"
    return f"name:{str(field.get('name') or '').casefold()}"

def _merge_custom_field(merged: dict[str, dict[str, Any]], field: dict[str, Any]) -> None:
    field_id = field.get("id")
    field_name = str(field.get("name") or "")
    if field_id in (None, "") and field_name:
        for existing in merged.values():
            if str(existing.get("name") or "").casefold() == field_name.casefold():
                existing.update({key: value for key, value in field.items() if value not in (None, "")})
                return
    merged[_custom_field_key(field)] = field

def _custom_field_from_named_value(name: str, raw_value: Any) -> dict[str, Any]:
    field: dict[str, Any] = {}
    if str(name).isdigit():
        field["id"] = int(str(name))
    else:
        field["name"] = str(name)
    if isinstance(raw_value, dict):
        if raw_value.get("id") not in (None, ""):
            field["id"] = redmine_optional_id(str(raw_value["id"]))
        if raw_value.get("name") not in (None, ""):
            field["name"] = str(raw_value["name"])
        field["value"] = raw_value.get("value")
    else:
        field["value"] = raw_value
    return field

def _parse_custom_field_string(value: str) -> dict[str, Any]:
    text = value.strip()
    if not text:
        raise RedmineError("Empty Redmine custom field override is not allowed.")
    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise RedmineError("Redmine custom field JSON override must be an object.")
        return parsed
    if "=" not in text:
        raise RedmineError('Redmine custom field override must use "name=value" or "id=value".')
    name, field_value = text.split("=", 1)
    return _custom_field_from_named_value(name.strip(), field_value.strip())

def normalize_redmine_custom_fields(raw_fields: Any) -> list[dict[str, Any]]:
    if raw_fields in (None, "", []):
        return []
    if isinstance(raw_fields, str):
        text = raw_fields.strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            return normalize_redmine_custom_fields(json.loads(text))
        return [_parse_custom_field_string(text)]
    if isinstance(raw_fields, dict):
        return [_custom_field_from_named_value(str(name), value) for name, value in raw_fields.items()]
    if isinstance(raw_fields, list):
        fields: list[dict[str, Any]] = []
        for item in raw_fields:
            if isinstance(item, str):
                fields.append(_parse_custom_field_string(item))
            elif isinstance(item, dict):
                fields.append(dict(item))
            else:
                raise RedmineError("Redmine custom field entries must be objects or name=value strings.")
        return fields
    raise RedmineError("Redmine custom fields must be an object, array, JSON string, or name=value string.")

def build_redmine_custom_fields(
    args: argparse.Namespace,
    template: dict[str, Any],
    header: dict[str, str],
    result: ParsedResult,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw_field in normalize_redmine_custom_fields(template.get("custom_fields")):
        field = render_redmine_template_value(raw_field, header, result, context)
        _merge_custom_field(merged, field)
    for raw_field in normalize_redmine_custom_fields(getattr(args, "redmine_custom_fields", None)):
        field = render_redmine_template_value(raw_field, header, result, context)
        _merge_custom_field(merged, field)

    missing_ids = [str(field.get("name") or field.get("value") or field) for field in merged.values() if field.get("id") in (None, "")]
    if missing_ids:
        raise RedmineError(
            "Redmine custom fields must include Redmine custom field IDs. Missing id for: "
            + ", ".join(missing_ids)
        )

    required = template.get("required_custom_fields") or []
    if not isinstance(required, list):
        raise RedmineError("Redmine template required_custom_fields must be an array.")
    missing_required: list[str] = []
    for raw_required in required:
        if isinstance(raw_required, dict):
            required_id = raw_required.get("id")
            required_name = str(raw_required.get("name") or required_id or "").strip()
        else:
            required_id = raw_required if str(raw_required).isdigit() else None
            required_name = str(raw_required).strip()
        matched = None
        for field in merged.values():
            if required_id is not None and str(field.get("id")) == str(required_id):
                matched = field
                break
            if required_name and str(field.get("name") or "").casefold() == required_name.casefold():
                matched = field
                break
        if matched is None or _is_blank_redmine_value(matched.get("value")):
            missing_required.append(required_name or str(required_id))
    if missing_required:
        raise RedmineError("Missing required Redmine custom fields: " + ", ".join(missing_required))

    custom_fields: list[dict[str, Any]] = []
    for field in merged.values():
        value = field.get("value")
        if _is_blank_redmine_value(value):
            continue
        custom_fields.append({"id": redmine_optional_id(str(field["id"])), "value": value})
    return custom_fields

def _redmine_template_field(
    args: argparse.Namespace,
    template: dict[str, Any],
    issue_field: str,
    arg_attr: str,
    env_name: str,
    header: dict[str, str],
    result: ParsedResult,
    context: dict[str, Any],
) -> Any:
    explicit = redmine_arg(args, arg_attr, env_name)
    if explicit:
        return explicit
    template_value = template.get(issue_field)
    if template_value in (None, ""):
        return ""
    return render_redmine_template_value(template_value, header, result, context)


def _template_has_field(template: dict[str, Any], issue_field: str) -> bool:
    return issue_field in template


def _redmine_text_override(args: argparse.Namespace, value_attr: str, file_attr: str) -> str:
    direct = getattr(args, value_attr, None)
    file_value = getattr(args, file_attr, None)
    if direct not in (None, "") and file_value not in (None, ""):
        raise RedmineError(
            f"--{value_attr.replace('_', '-')} and --{file_attr.replace('_', '-')} cannot be used together."
        )
    if file_value not in (None, ""):
        path = Path(str(file_value))
        if not path.exists():
            raise RedmineError(f"Redmine text file does not exist: {path}")
        return path.read_text(encoding="utf-8").strip()
    return str(direct or "").strip()


def build_redmine_subject(
    args: argparse.Namespace,
    header: dict[str, str],
    result: ParsedResult,
    context: dict[str, Any],
) -> str:
    override = _redmine_text_override(args, "redmine_subject", "redmine_subject_file")
    if override:
        return truncate_text(override, 255)
    build_name = str(context["build"].get("name") or header.get("EMS Version") or "")
    suffix = f" - {build_name}" if build_name else ""
    return truncate_text(f"[{result.external_id}] {result.test_name} Result {result.raw_status}{suffix}", 255)

def build_redmine_dedupe(
    redmine_project_id: str,
    result: ParsedResult,
    context: dict[str, Any],
) -> dict[str, str]:
    key = build_dedupe_key(
        redmine_project_id=redmine_project_id,
        context=context,
        result=result,
        failure_summary="Result Fail" if result.status == "f" else result.raw_status,
    )
    digest = dedupe_digest(key)
    return {
        "key": key,
        "digest": digest,
        "marker": dedupe_marker(digest),
    }

def build_redmine_group_dedupe(
    redmine_project_id: str,
    results: list[ParsedResult],
    context: dict[str, Any],
) -> dict[str, str]:
    if not results:
        raise RedmineError("Grouped Redmine dedupe requires at least one failed result.")
    ordered = sorted(results, key=lambda item: item.external_id)
    members = "|".join(f"{item.external_id}:{item.test_name}:{item.raw_status}" for item in ordered)
    key = build_dedupe_key(
        redmine_project_id=redmine_project_id,
        context=context,
        result=ordered[0],
        failure_summary=f"Grouped failures: {members}",
    )
    key = f"{key}\ngroup_members={members}"
    digest = dedupe_digest(key)
    return {
        "key": key,
        "digest": digest,
        "marker": dedupe_marker(digest),
    }


def build_redmine_description(
    args: argparse.Namespace,
    header: dict[str, str],
    report_path: Path,
    result: ParsedResult,
    context: dict[str, Any],
    dedupe: dict[str, str] | None = None,
) -> str:
    override = _redmine_text_override(args, "redmine_description", "redmine_description_file")
    if override:
        if dedupe is None:
            return override
        return "\n".join(
            [
                override,
                "",
                "Traceability:",
                f"- Dedupe Key: {dedupe['marker']}",
                f"- Dedupe Digest: {dedupe['digest']}",
            ]
        )
    lines = [
        "Automation failure created from TestLink Agent CLI.",
        "",
        "TestLink:",
        f"- Test case: {result.external_id}",
        f"- Test case name: {result.testlink_name or ''}",
        f"- Test project: {context['project'].get('name') or ''}",
        f"- Test plan: {context['plan'].get('name') or ''}",
        f"- Platform: {context['platform'].get('name') or ''}",
        f"- Build: {context['build'].get('name') or ''}",
        "",
        "Automation:",
        f"- Test function: {result.test_name}",
        f"- Original result: Result {result.raw_status}",
        f"- Duration: {result.duration_text}",
        f"- Report file: {report_path.name}",
    ]
    if dedupe is not None:
        lines.extend(
            [
                "",
                "Traceability:",
                f"- Dedupe Key: {dedupe['marker']}",
                f"- Dedupe Digest: {dedupe['digest']}",
            ]
        )
    for label in (
        "Report generated on",
        "EMS Version",
        "Node Name",
        "Node IP",
        "Node Chassis",
        "UI URL",
        "Test Target Source",
    ):
        value = header.get(label)
        if value:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)

def build_redmine_evidence_comment(
    header: dict[str, str],
    report_path: Path,
    result: ParsedResult,
    context: dict[str, Any],
    *,
    dedupe_marker_value: str | None = None,
    testlink_response: dict[str, Any] | None = None,
) -> str:
    lines = [
        "Automation retest evidence from TestLink Agent.",
        "",
        "TestLink:",
        f"- Test case: {result.external_id}",
        f"- Test case name: {result.testlink_name or ''}",
        f"- Test project: {context['project'].get('name') or ''}",
        f"- Test plan: {context['plan'].get('name') or ''}",
        f"- Platform: {context['platform'].get('name') or ''}",
        f"- Build: {context['build'].get('name') or ''}",
        "",
        "Automation:",
        f"- Test function: {result.test_name}",
        f"- Result: {result.raw_status}",
        f"- Duration: {result.duration_text}",
        f"- Report file: {report_path.name}",
    ]
    if dedupe_marker_value:
        lines.append(f"- Dedupe Key: {dedupe_marker_value}")
    if testlink_response:
        execution_id = (
            testlink_response.get("execution_id")
            or testlink_response.get("executionId")
            or testlink_response.get("id")
        )
        if execution_id:
            lines.append(f"- TestLink execution ID: {execution_id}")
    for label in (
        "Report generated on",
        "EMS Version",
        "Node Name",
        "Node IP",
        "Node Chassis",
        "UI URL",
        "Test Target Source",
    ):
        value = header.get(label)
        if value:
            lines.append(f"- {label}: {value}")
    lines.extend(
        [
            "",
            "Note: TestLink Agent only adds evidence comments. It does not change Redmine status, assignee, or fixed version.",
        ]
    )
    return "\n".join(lines)

def build_redmine_issue_payload(
    args: argparse.Namespace,
    header: dict[str, str],
    report_path: Path,
    result: ParsedResult,
    context: dict[str, Any],
    *,
    group_results: list[ParsedResult] | None = None,
) -> dict[str, Any]:
    reject_restricted_issue_fields(args)
    template = load_redmine_template(args)
    project_id = _redmine_template_field(
        args,
        template,
        "project_id",
        "redmine_project",
        "REDMINE_PROJECT_ID",
        header,
        result,
        context,
    )
    if not project_id:
        raise RedmineError("REDMINE_PROJECT_ID or --redmine-project is required when --redmine-create-bugs is used.")

    issue_dedupe = (
        build_redmine_group_dedupe(str(project_id), group_results, context)
        if group_results
        else build_redmine_dedupe(str(project_id), result, context)
    )
    issue: dict[str, Any] = {
        "project_id": project_id,
        "subject": build_redmine_subject(args, header, result, context),
        "description": build_redmine_description(
            args,
            header,
            report_path,
            result,
            context,
            issue_dedupe,
        ),
    }
    optional_fields = {
        "tracker_id": _redmine_template_field(
            args, template, "tracker_id", "redmine_tracker_id", "REDMINE_TRACKER_ID", header, result, context
        ),
        "status_id": _redmine_template_field(
            args, template, "status_id", "redmine_status_id", "REDMINE_STATUS_ID", header, result, context
        ),
        "priority_id": _redmine_template_field(
            args, template, "priority_id", "redmine_priority_id", "REDMINE_PRIORITY_ID", header, result, context
        ),
        "category_id": _redmine_template_field(
            args, template, "category_id", "redmine_category_id", "REDMINE_CATEGORY_ID", header, result, context
        ),
    }
    if manager_fields_enabled():
        optional_fields.update(redmine_manager_fields(args))
    if _template_has_field(template, "fixed_version_id"):
        fixed_version_id = _redmine_template_field(
            args,
            template,
            "fixed_version_id",
            "redmine_fixed_version_id",
            "REDMINE_FIXED_VERSION_ID",
            header,
            result,
            context,
        )
        if fixed_version_id:
            if not manager_fields_enabled():
                raise RedmineError(
                    "Restricted Redmine field fixed_version_id is not allowed for automation-created bugs. "
                    "Set REDMINE_ALLOW_MANAGER_FIELDS=true only on a manager-owned machine to allow it."
                )
            optional_fields["fixed_version_id"] = fixed_version_id
    for field, value in optional_fields.items():
        coerced = redmine_optional_id(value)
        if coerced is not None:
            issue[field] = coerced
    custom_fields = build_redmine_custom_fields(args, template, header, result, context)
    if custom_fields:
        issue["custom_fields"] = custom_fields
    return issue

def redmine_issue_to_dict(issue: RedmineIssue | None) -> dict[str, Any] | None:
    if issue is None:
        return None
    return {
        "id": issue.id,
        "url": issue.url,
        "subject": issue.subject,
        "reused": issue.reused,
        "dedupe_marker": issue.dedupe_marker,
    }

def redmine_issue_from_dict(value: dict[str, Any] | None) -> RedmineIssue | None:
    if not isinstance(value, dict) or not value.get("id"):
        return None
    return RedmineIssue(
        id=str(value.get("id")),
        url=str(value.get("url") or ""),
        subject=str(value.get("subject") or ""),
        reused=bool(value.get("reused", True)),
        dedupe_marker=str(value.get("dedupe_marker") or "") or None,
    )

def build_existing_redmine_issue(args: argparse.Namespace, result: ParsedResult) -> RedmineIssue | None:
    issue_id = str(getattr(args, "redmine_issue_id", "") or "").strip()
    if not issue_id:
        return None
    issue_url = str(getattr(args, "redmine_issue_url", "") or "").strip()
    if not issue_url:
        base_url = redmine_arg(args, "redmine_url", "REDMINE_URL")
        if base_url:
            issue_url = f"{base_url.rstrip('/')}/issues/{issue_id}"
    return RedmineIssue(
        id=issue_id,
        url=issue_url,
        subject=f"[{result.external_id}] Existing Redmine issue #{issue_id}",
        reused=True,
    )

def build_notes(
    header: dict[str, str],
    report_path: Path,
    result: ParsedResult,
    redmine_issue: RedmineIssue | None = None,
    dedupe_marker_value: str | None = None,
) -> str:
    generated = header.get("Report generated on", "")
    ems_version = header.get("EMS Version", "")
    node_name = header.get("Node Name", "")
    node_ip = header.get("Node IP", "")

    lines = [
        "Automation Source: Web EMS Rest API automation report",
        f"Report generated time: {generated}",
        f"EMS Version: {ems_version}",
        f"Node: {node_name} / {node_ip}",
        f"Test function: {result.test_name}",
        f"Original result: Result {result.raw_status}",
        f"Duration: {result.duration_text}",
        f"Report file: {report_path.name}",
    ]
    if result.status == "f":
        lines.append("Failure summary: Result Fail")
    if dedupe_marker_value:
        lines.append(f"Dedupe Key: {dedupe_marker_value}")
    if redmine_issue is not None:
        lines.extend(
            [
                f"REDMINE-ID: #{redmine_issue.id}",
                f"REDMINE-URL: {redmine_issue.url}",
                f"REDMINE-REUSED: {'yes' if redmine_issue.reused else 'no'}",
            ]
        )
    return "\n".join(lines)
