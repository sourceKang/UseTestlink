from __future__ import annotations

import json
import sys
from typing import Any

from .api import call_tool
from .config import DEFAULT_CATALOG_PATH, DEFAULT_PROFILES_PATH, DEFAULT_TIMEOUT_SECONDS


JSON_OBJECT: dict[str, Any] = {"type": "object", "additionalProperties": False}
COMMON_PROPERTIES: dict[str, Any] = {
    "url": {"type": "string", "description": "TestLink base URL or XML-RPC endpoint. Defaults to TESTLINK_URL."},
    "devkey": {"type": "string", "description": "Personal API key. Prefer TESTLINK_DEVKEY or env_file."},
    "env_file": {"type": "string", "description": "Optional env file path."},
    "timeout": {"type": "integer", "default": DEFAULT_TIMEOUT_SECONDS, "minimum": 1},
}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        **JSON_OBJECT,
        "properties": {**COMMON_PROPERTIES, **properties},
        "required": required or [],
    }


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "testlink_list_projects",
        "description": "List visible TestLink projects.",
        "inputSchema": _schema({}),
    },
    {
        "name": "testlink_list_plans",
        "description": "List test plans for a TestLink project.",
        "inputSchema": _schema({"project": _string("Exact TestLink project name.")}, ["project"]),
    },
    {
        "name": "testlink_list_platforms",
        "description": "List platforms for a project and test plan.",
        "inputSchema": _schema(
            {"project": _string("Exact TestLink project name."), "plan": _string("Exact test plan name.")},
            ["project", "plan"],
        ),
    },
    {
        "name": "testlink_list_builds",
        "description": "List builds for a project and test plan, newest first.",
        "inputSchema": _schema(
            {
                "project": _string("Exact TestLink project name."),
                "plan": _string("Exact test plan name."),
                "open_only": {"type": "boolean", "default": False},
            },
            ["project", "plan"],
        ),
    },
    {
        "name": "testlink_list_suites",
        "description": "List test suites for a project.",
        "inputSchema": _schema(
            {
                "project": _string("Exact TestLink project name."),
                "parent_suite_id": _string("Optional parent suite ID."),
                "recursive": {"type": "boolean", "default": True},
            },
            ["project"],
        ),
    },
    {
        "name": "testlink_find_suites",
        "description": "Search projects and suites using a local catalog or TestLink refresh.",
        "inputSchema": _schema(
            {
                "project_contains": _string("Optional project name substring."),
                "suite_contains": _string("Optional suite name/path substring."),
                "active_only": {"type": "boolean", "default": True},
                "recursive": {"type": "boolean", "default": True},
                "max_projects": {"type": "integer", "default": 20, "minimum": 1},
                "catalog": {"type": "string", "default": DEFAULT_CATALOG_PATH},
                "refresh": {"type": "boolean", "default": False},
                "offline": {"type": "boolean", "default": False},
            },
        ),
    },
    {
        "name": "testlink_refresh_catalog",
        "description": "Download project and suite catalog for faster local search.",
        "inputSchema": _schema(
            {
                "out": {"type": "string", "default": DEFAULT_CATALOG_PATH},
                "project_contains": _string("Optional project name substring."),
                "active_only": {"type": "boolean", "default": True},
                "recursive": {"type": "boolean", "default": True},
                "max_projects": {"type": "integer", "default": 20, "minimum": 1},
                "force": {"type": "boolean", "default": False},
            },
        ),
    },
    {
        "name": "testlink_download_testcases",
        "description": "Download test cases for a project, plan, and platform as JSON or XLSX.",
        "inputSchema": _schema(
            {
                "project": _string("Exact TestLink project name."),
                "plan": _string("Exact test plan name."),
                "platform": _string("Exact platform name."),
                "details": {"type": "string", "enum": ["simple", "full"], "default": "simple"},
                "format": {"type": "string", "enum": ["auto", "json", "xlsx"], "default": "auto"},
                "out": _string("Optional output file path. JSON prints to result when omitted."),
                "force": {"type": "boolean", "default": False},
            },
            ["project", "plan", "platform"],
        ),
    },
    {
        "name": "testlink_list_profiles",
        "description": "List saved local project/suite profiles.",
        "inputSchema": _schema({"profiles": {"type": "string", "default": DEFAULT_PROFILES_PATH}}),
    },
    {
        "name": "testlink_save_profile",
        "description": "Save a reusable local project/suite profile.",
        "inputSchema": _schema(
            {
                "name": _string("Profile name."),
                "project": _string("Exact TestLink project name."),
                "suite_id": _string("Target suite ID."),
                "suite_name": _string("Exact suite name/path."),
                "project_contains": _string("Search project names by substring."),
                "suite_contains": _string("Search suite names or paths by substring."),
                "profiles": {"type": "string", "default": DEFAULT_PROFILES_PATH},
                "catalog": {"type": "string", "default": DEFAULT_CATALOG_PATH},
                "refresh": {"type": "boolean", "default": False},
                "offline": {"type": "boolean", "default": False},
                "force": {"type": "boolean", "default": False},
            },
            ["name"],
        ),
    },
    {
        "name": "testlink_delete_profile",
        "description": "Delete a saved local project/suite profile.",
        "inputSchema": _schema(
            {"name": _string("Profile name."), "profiles": {"type": "string", "default": DEFAULT_PROFILES_PATH}},
            ["name"],
        ),
    },
    {
        "name": "testlink_create_testcase",
        "description": "Preview or create one TestLink test case. Defaults to preview; set write true only after review.",
        "inputSchema": _schema(
            {
                "profile": _string("Saved local target profile."),
                "profiles": {"type": "string", "default": DEFAULT_PROFILES_PATH},
                "project": _string("Exact TestLink project name."),
                "suite_id": _string("Target suite ID."),
                "suite_name": _string("Exact suite name/path."),
                "name": _string("Test case title."),
                "author_login": _string("TestLink author login. Defaults to TESTLINK_AUTHOR_LOGIN."),
                "summary": {"type": "string", "default": ""},
                "summary_file": _string("UTF-8 file containing summary."),
                "preconditions": {"type": "string", "default": ""},
                "preconditions_file": _string("UTF-8 file containing preconditions."),
                "steps": {"type": "array", "items": {"type": "string"}, "description": "Step strings like 'Action => Expected result'."},
                "steps_file": _string("JSON array of step strings or objects."),
                "importance": {"type": "string", "default": "medium"},
                "execution_type": {"type": "string", "default": "manual"},
                "order": {"type": "integer"},
                "duplicate_action": {"type": "string", "enum": ["block", "generate-new"], "default": "block"},
                "write": {"type": "boolean", "default": False},
            },
            ["name"],
        ),
    },
    {
        "name": "testlink_update_testcase",
        "description": "Preview or update one TestLink test case. Defaults to preview; set write true only after review.",
        "inputSchema": _schema(
            {
                "profile": _string("Saved local target profile."),
                "profiles": {"type": "string", "default": DEFAULT_PROFILES_PATH},
                "project": _string("Exact TestLink project name for preview context."),
                "suite_id": _string("Target suite ID for preview context."),
                "suite_name": _string("Exact suite name/path for preview context."),
                "testcase_id": _string("Internal TestLink testcase ID."),
                "testcase_external_id": _string("External testcase ID, for example GW-123."),
                "version": _string("Optional testcase version."),
                "name": _string("New test case title."),
                "summary": _string("New summary."),
                "summary_file": _string("UTF-8 file containing new summary."),
                "preconditions": _string("New preconditions."),
                "preconditions_file": _string("UTF-8 file containing new preconditions."),
                "steps": {"type": "array", "items": {"type": "string"}},
                "steps_file": _string("JSON array of replacement steps."),
                "importance": _string("low, medium, high, or numeric TestLink value."),
                "execution_type": _string("manual, automated, or numeric TestLink value."),
                "write": {"type": "boolean", "default": False},
            },
        ),
    },
    {
        "name": "testlink_upload_report",
        "description": "Preview or upload an automation report. Defaults to preview; set write true only after review.",
        "inputSchema": _schema(
            {
                "project": _string("Exact TestLink project name."),
                "plan": _string("Exact test plan name."),
                "platform": _string("Exact platform name."),
                "build": _string("Build name. Omit with build_id to use latest active/open build."),
                "build_id": _string("Build ID. Omit with build to use latest active/open build."),
                "report": _string("Automation report file path."),
                "skip_policy": {"type": "string", "enum": ["ignore", "blocked"], "default": "ignore"},
                "write": {"type": "boolean", "default": False},
                "require_open_build": {"type": "boolean", "default": True},
                "redmine_create_bugs": {"type": "boolean", "default": False},
                "redmine_url": _string("Redmine base URL. Defaults to REDMINE_URL."),
                "redmine_api_key": _string("Redmine API key. Prefer REDMINE_API_KEY."),
                "redmine_project": _string("Redmine project identifier or ID."),
                "redmine_tracker_id": _string("Redmine tracker ID."),
                "redmine_status_id": _string("Redmine status ID."),
                "redmine_priority_id": _string("Redmine priority ID."),
                "redmine_assigned_to_id": _string("Redmine assignee ID."),
                "redmine_category_id": _string("Redmine category ID."),
                "redmine_fixed_version_id": _string("Redmine target version ID."),
                "redmine_issue_id": _string("Existing Redmine issue ID to record for failed results."),
                "redmine_issue_url": _string("Existing Redmine issue URL to record in notes."),
                "redmine_dedupe": {"type": "string", "enum": ["open", "none"], "default": "open"},
                "testlink_bug_link": {"type": "string", "enum": ["bugid", "notes", "both"], "default": "notes"},
            },
            ["project", "plan", "platform", "report"],
        ),
    },
]


def _result_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if request_id is None and str(method).startswith("notifications/"):
        return None

    if method == "initialize":
        return _result_response(
            request_id,
            {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "testlink-agent", "version": "0.1.0"},
            },
        )
    if method == "ping":
        return _result_response(request_id, {})
    if method == "tools/list":
        return _result_response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        result = call_tool(str(tool_name), arguments if isinstance(arguments, dict) else {})
        return _result_response(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False, default=str)}],
                "isError": not bool(result.get("ok")),
            },
        )
    return _error_response(request_id, -32601, f"Method not found: {method}")


def _read_message() -> tuple[dict[str, Any] | None, str]:
    first_line = sys.stdin.buffer.readline()
    if not first_line:
        return None, "line"

    if first_line.lower().startswith(b"content-length:"):
        headers = [first_line]
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None, "content-length"
            if line in (b"\r\n", b"\n"):
                break
            headers.append(line)

        content_length = 0
        for header in headers:
            name, _, value = header.decode("ascii", errors="replace").partition(":")
            if name.casefold() == "content-length":
                content_length = int(value.strip())
                break
        body = sys.stdin.buffer.read(content_length)
        return json.loads(body.decode("utf-8")), "content-length"

    line = first_line.decode("utf-8").strip()
    if not line:
        return {}, "line"
    return json.loads(line), "line"


def _write_response(response: dict[str, Any], framing: str) -> None:
    payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
    if framing == "content-length":
        sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return
    print(payload.decode("utf-8"), flush=True)


def run() -> int:
    while True:
        try:
            message, framing = _read_message()
            if message is None:
                break
            if not message:
                continue
            response = handle_request(message)
        except Exception as exc:
            framing = "line"
            response = _error_response(None, -32603, str(exc))
        if response is not None:
            _write_response(response, framing)
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
