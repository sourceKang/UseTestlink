from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import DEFAULT_TIMEOUT_SECONDS
from .errors import RedmineError
from .models import ParsedResult, RedmineIssue


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
    return os.environ.get("REDMINE_ALLOW_MANAGER_FIELDS", "").strip().casefold() in {"1", "true", "yes", "on"}

def redmine_manager_fields(args: argparse.Namespace) -> dict[str, str]:
    return {
        "assigned_to_id": redmine_arg(args, "redmine_assigned_to_id", "REDMINE_ASSIGNED_TO_ID"),
        "fixed_version_id": redmine_arg(args, "redmine_fixed_version_id", "REDMINE_FIXED_VERSION_ID"),
    }

def reject_restricted_issue_fields(args: argparse.Namespace) -> None:
    if manager_fields_enabled():
        return
    blocked = [field for field, value in redmine_manager_fields(args).items() if value]
    if blocked:
        joined = ", ".join(blocked)
        raise RedmineError(
            f"Restricted Redmine fields are not allowed for automation-created bugs: {joined}. "
            "Set REDMINE_ALLOW_MANAGER_FIELDS=true only on a manager-owned machine to allow them."
        )

def build_redmine_subject(header: dict[str, str], result: ParsedResult, context: dict[str, Any]) -> str:
    build_name = str(context["build"].get("name") or header.get("EMS Version") or "")
    suffix = f" - {build_name}" if build_name else ""
    return truncate_text(f"[{result.external_id}] {result.test_name} Result {result.raw_status}{suffix}", 255)

def build_redmine_description(
    header: dict[str, str],
    report_path: Path,
    result: ParsedResult,
    context: dict[str, Any],
) -> str:
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

def build_redmine_issue_payload(
    args: argparse.Namespace,
    header: dict[str, str],
    report_path: Path,
    result: ParsedResult,
    context: dict[str, Any],
) -> dict[str, Any]:
    reject_restricted_issue_fields(args)
    project_id = redmine_arg(args, "redmine_project", "REDMINE_PROJECT_ID")
    if not project_id:
        raise RedmineError("REDMINE_PROJECT_ID or --redmine-project is required when --redmine-create-bugs is used.")

    issue: dict[str, Any] = {
        "project_id": project_id,
        "subject": build_redmine_subject(header, result, context),
        "description": build_redmine_description(header, report_path, result, context),
    }
    optional_fields = {
        "tracker_id": redmine_arg(args, "redmine_tracker_id", "REDMINE_TRACKER_ID"),
        "status_id": redmine_arg(args, "redmine_status_id", "REDMINE_STATUS_ID"),
        "priority_id": redmine_arg(args, "redmine_priority_id", "REDMINE_PRIORITY_ID"),
        "category_id": redmine_arg(args, "redmine_category_id", "REDMINE_CATEGORY_ID"),
    }
    if manager_fields_enabled():
        optional_fields.update(redmine_manager_fields(args))
    for field, value in optional_fields.items():
        coerced = redmine_optional_id(value)
        if coerced is not None:
            issue[field] = coerced
    return issue

def redmine_issue_to_dict(issue: RedmineIssue | None) -> dict[str, Any] | None:
    if issue is None:
        return None
    return {
        "id": issue.id,
        "url": issue.url,
        "subject": issue.subject,
        "reused": issue.reused,
    }

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
    if redmine_issue is not None:
        lines.extend(
            [
                f"REDMINE-ID: #{redmine_issue.id}",
                f"REDMINE-URL: {redmine_issue.url}",
                f"REDMINE-REUSED: {'yes' if redmine_issue.reused else 'no'}",
            ]
        )
    return "\n".join(lines)
