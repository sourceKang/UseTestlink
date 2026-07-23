from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .errors import RedmineMcpError
from .models import RedmineIssue


class RedmineClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: int = 60):
        if not base_url:
            raise RedmineMcpError("REDMINE_URL is required.", code="CONFIG_MISSING")
        if not api_key:
            raise RedmineMcpError("REDMINE_API_KEY is required.", code="CONFIG_MISSING")
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
            raise RedmineMcpError(
                f"Redmine HTTP {exc.code}: {response_body}",
                code=f"HTTP_{exc.code}",
                retryable=exc.code >= 500,
            ) from exc
        except urllib.error.URLError as exc:
            raise RedmineMcpError(
                f"Redmine connection failed: {exc.reason}",
                code="CONNECTION_FAILED",
                retryable=True,
            ) from exc
        if not response_body:
            return {}
        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RedmineMcpError(
                f"Redmine returned non-JSON response: {response_body}",
                code="INVALID_RESPONSE",
            ) from exc
        if not isinstance(parsed, dict):
            raise RedmineMcpError("Redmine response must be a JSON object.", code="INVALID_RESPONSE")
        return parsed

    def request_binary_json(
        self,
        method: str,
        path: str,
        content: bytes,
        *,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query_items = {key: value for key, value in (query or {}).items() if value not in (None, "")}
        url = f"{self.base_url}{path}"
        if query_items:
            url = f"{url}?{urllib.parse.urlencode(query_items)}"
        request = urllib.request.Request(
            url,
            data=content,
            method=method,
            headers={
                "Content-Type": "application/octet-stream",
                "Accept": "application/json",
                "X-Redmine-API-Key": self.api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise RedmineMcpError(
                f"Redmine upload HTTP {exc.code}: {response_body}",
                code=f"HTTP_{exc.code}",
                retryable=exc.code >= 500,
            ) from exc
        except urllib.error.URLError as exc:
            raise RedmineMcpError(
                f"Redmine upload connection failed: {exc.reason}",
                code="CONNECTION_FAILED",
                retryable=True,
            ) from exc
        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RedmineMcpError(
                "Redmine upload returned a non-JSON response.",
                code="INVALID_RESPONSE",
            ) from exc
        if not isinstance(parsed, dict):
            raise RedmineMcpError("Redmine upload response must be a JSON object.", code="INVALID_RESPONSE")
        return parsed

    def health(self) -> dict[str, Any]:
        return self.request_json("GET", "/users/current.json")

    def get_project_metadata(self, project_id: str) -> dict[str, Any]:
        encoded_project = urllib.parse.quote(str(project_id), safe="")
        return {
            "project": self.request_json("GET", f"/projects/{encoded_project}.json").get("project") or {},
            "trackers": self.request_json("GET", "/trackers.json").get("trackers") or [],
            "priorities": self.request_json("GET", "/enumerations/issue_priorities.json").get(
                "issue_priorities"
            ) or [],
            "custom_fields": self.request_json("GET", "/custom_fields.json").get("custom_fields") or [],
            "statuses": self.request_json("GET", "/issue_statuses.json").get("issue_statuses") or [],
        }

    def find_issues(
        self,
        *,
        project_id: str,
        status_id: str = "open",
        tracker_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        response = self.request_json(
            "GET",
            "/issues.json",
            query={
                "project_id": project_id,
                "status_id": status_id,
                "tracker_id": tracker_id,
                "limit": min(max(int(limit), 1), 100),
                "sort": "updated_on:desc",
            },
        )
        issues = response.get("issues") or []
        return [issue for issue in issues if isinstance(issue, dict)]

    def find_issue_by_marker(
        self,
        *,
        project_id: str,
        marker: str,
        status_id: str,
        tracker_id: str | None = None,
    ) -> RedmineIssue | None:
        for issue in self.find_issues(project_id=project_id, status_id=status_id, tracker_id=tracker_id):
            haystack = "\n".join(str(issue.get(field) or "") for field in ("subject", "description"))
            if marker not in haystack or "id" not in issue:
                continue
            issue_id = str(issue["id"])
            return RedmineIssue(
                id=issue_id,
                url=self.issue_url(issue_id),
                subject=str(issue.get("subject") or ""),
                state="closed" if status_id == "closed" else "open",
                reused=status_id == "open",
            )
        return None

    def create_issue(self, issue_payload: dict[str, Any]) -> RedmineIssue:
        response = self.request_json("POST", "/issues.json", {"issue": issue_payload})
        issue = response.get("issue")
        if not isinstance(issue, dict) or "id" not in issue:
            raise RedmineMcpError(f"Unexpected Redmine create response: {response}", code="INVALID_RESPONSE")
        issue_id = str(issue["id"])
        return RedmineIssue(
            id=issue_id,
            url=self.issue_url(issue_id),
            subject=str(issue.get("subject") or issue_payload.get("subject") or ""),
            state="open",
            reused=False,
        )

    def upload_attachment(self, *, filename: str, content: bytes) -> str:
        response = self.request_binary_json(
            "POST",
            "/uploads.json",
            content,
            query={"filename": filename},
        )
        upload = response.get("upload")
        token = str(upload.get("token") or "").strip() if isinstance(upload, dict) else ""
        if not token:
            raise RedmineMcpError(
                "Redmine upload response did not include an upload token.",
                code="INVALID_RESPONSE",
            )
        return token

    def add_comment(self, issue_id: str | int, notes: str) -> dict[str, Any]:
        if not str(issue_id).strip():
            raise RedmineMcpError("Redmine issue ID is required.", code="INVALID_ARGUMENT")
        if not notes.strip():
            raise RedmineMcpError("Redmine comment must not be empty.", code="INVALID_ARGUMENT")
        return self.request_json("PUT", f"/issues/{issue_id}.json", {"issue": {"notes": notes}})

    def get_issue_journals(self, issue_id: str | int) -> list[dict[str, Any]]:
        response = self.request_json(
            "GET",
            f"/issues/{issue_id}.json",
            query={"include": "journals"},
        )
        issue = response.get("issue") if isinstance(response.get("issue"), dict) else {}
        journals = issue.get("journals") if isinstance(issue, dict) else []
        return [journal for journal in journals or [] if isinstance(journal, dict)]
