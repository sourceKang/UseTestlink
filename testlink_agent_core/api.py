from __future__ import annotations

import argparse
import contextlib
import io
import json
import xmlrpc.client
from typing import Any, Callable

from .commands import (
    command_create_testcase,
    command_delete_profile,
    command_download_testcases,
    command_find_suites,
    command_list_builds,
    command_list_platforms,
    command_list_plans,
    command_list_profiles,
    command_list_projects,
    command_list_suites,
    command_refresh_catalog,
    command_save_profile,
    command_update_testcase,
    command_upload_report,
)
from .config import DEFAULT_CATALOG_PATH, DEFAULT_PROFILES_PATH, DEFAULT_TIMEOUT_SECONDS


CommandHandler = Callable[[argparse.Namespace], int]

_SECRET_KEY_PARTS = ("devkey", "api_key", "password", "token", "secret")

_BASE_DEFAULTS: dict[str, Any] = {
    "url": None,
    "devkey": None,
    "env_file": None,
    "timeout": DEFAULT_TIMEOUT_SECONDS,
    "project": None,
    "plan": None,
    "platform": None,
    "build": None,
    "build_id": None,
    "report": None,
    "skip_policy": "ignore",
    "write": False,
    "require_open_build": True,
    "progress": 0,
    "throttle": 0.03,
    "redmine_create_bugs": False,
    "redmine_url": None,
    "redmine_api_key": None,
    "redmine_project": None,
    "redmine_tracker_id": None,
    "redmine_status_id": None,
    "redmine_priority_id": None,
    "redmine_assigned_to_id": None,
    "redmine_category_id": None,
    "redmine_fixed_version_id": None,
    "redmine_issue_id": None,
    "redmine_issue_url": None,
    "redmine_dedupe": "open",
    "testlink_bug_link": "notes",
    "open_only": False,
    "parent_suite_id": None,
    "recursive": True,
    "project_contains": None,
    "suite_contains": None,
    "active_only": True,
    "max_projects": 20,
    "catalog": DEFAULT_CATALOG_PATH,
    "refresh": False,
    "offline": False,
    "out": None,
    "force": False,
    "details": "simple",
    "format": "auto",
    "profile": None,
    "profiles": DEFAULT_PROFILES_PATH,
    "suite_id": None,
    "suite_name": None,
    "name": None,
    "author_login": None,
    "summary": "",
    "summary_file": None,
    "preconditions": "",
    "preconditions_file": None,
    "step": None,
    "steps_file": None,
    "importance": "medium",
    "execution_type": "manual",
    "order": None,
    "duplicate_action": "block",
    "testcase_id": None,
    "testcase_external_id": None,
    "version": None,
}


def _args(**kwargs: Any) -> argparse.Namespace:
    values = dict(_BASE_DEFAULTS)
    values.update(kwargs)
    if "steps" in values:
        values["step"] = values.pop("steps")
    return argparse.Namespace(**values)


def _redact(value: Any, key: str | None = None) -> Any:
    if key and any(part in key.casefold() for part in _SECRET_KEY_PARTS):
        return "***redacted***" if value not in (None, "") else value
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _parse_json_stream(text: str) -> tuple[Any, list[Any], str | None]:
    text = text.strip()
    if not text:
        return None, [], None

    decoder = json.JSONDecoder()
    index = 0
    values: list[Any] = []
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        try:
            value, next_index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            return None, [], text
        values.append(value)
        index = next_index

    if not values:
        return None, [], text
    return values[-1], values[:-1], None


def _run_command(handler: CommandHandler, **kwargs: Any) -> dict[str, Any]:
    args = _args(**kwargs)
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = handler(args)
    except xmlrpc.client.Fault as fault:
        return {
            "ok": False,
            "code": 2,
            "error": {"type": "TestLinkFault", "code": fault.faultCode, "message": fault.faultString},
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": 1,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }

    result, events, raw_output = _parse_json_stream(stdout.getvalue())
    payload: dict[str, Any] = {
        "ok": code == 0,
        "code": code,
        "result": result,
    }
    if events:
        payload["events"] = events
    if raw_output is not None:
        payload["stdout"] = raw_output
    stderr_text = stderr.getvalue().strip()
    if stderr_text:
        payload["stderr"] = stderr_text
    return _redact(payload)


def list_projects(**kwargs: Any) -> dict[str, Any]:
    return _run_command(command_list_projects, **kwargs)


def list_plans(project: str, **kwargs: Any) -> dict[str, Any]:
    return _run_command(command_list_plans, project=project, **kwargs)


def list_platforms(project: str, plan: str, **kwargs: Any) -> dict[str, Any]:
    return _run_command(command_list_platforms, project=project, plan=plan, **kwargs)


def list_builds(project: str, plan: str, open_only: bool = False, **kwargs: Any) -> dict[str, Any]:
    return _run_command(command_list_builds, project=project, plan=plan, open_only=open_only, **kwargs)


def list_suites(
    project: str,
    parent_suite_id: str | None = None,
    recursive: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_command(
        command_list_suites,
        project=project,
        parent_suite_id=parent_suite_id,
        recursive=recursive,
        **kwargs,
    )


def find_suites(**kwargs: Any) -> dict[str, Any]:
    return _run_command(command_find_suites, **kwargs)


def refresh_catalog(**kwargs: Any) -> dict[str, Any]:
    return _run_command(command_refresh_catalog, **kwargs)


def download_testcases(project: str, plan: str, platform: str, **kwargs: Any) -> dict[str, Any]:
    return _run_command(
        command_download_testcases,
        project=project,
        plan=plan,
        platform=platform,
        **kwargs,
    )


def list_profiles(**kwargs: Any) -> dict[str, Any]:
    return _run_command(command_list_profiles, **kwargs)


def save_profile(name: str, **kwargs: Any) -> dict[str, Any]:
    return _run_command(command_save_profile, name=name, **kwargs)


def delete_profile(name: str, **kwargs: Any) -> dict[str, Any]:
    return _run_command(command_delete_profile, name=name, **kwargs)


def create_testcase(name: str, **kwargs: Any) -> dict[str, Any]:
    return _run_command(command_create_testcase, name=name, **kwargs)


def update_testcase(**kwargs: Any) -> dict[str, Any]:
    values = {
        "name": None,
        "summary": None,
        "preconditions": None,
        "importance": None,
        "execution_type": None,
    }
    values.update(kwargs)
    return _run_command(command_update_testcase, **values)


def upload_report(project: str, plan: str, platform: str, report: str, **kwargs: Any) -> dict[str, Any]:
    return _run_command(
        command_upload_report,
        project=project,
        plan=plan,
        platform=platform,
        report=report,
        **kwargs,
    )


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "testlink_list_projects": list_projects,
    "testlink_list_plans": list_plans,
    "testlink_list_platforms": list_platforms,
    "testlink_list_builds": list_builds,
    "testlink_list_suites": list_suites,
    "testlink_find_suites": find_suites,
    "testlink_refresh_catalog": refresh_catalog,
    "testlink_download_testcases": download_testcases,
    "testlink_list_profiles": list_profiles,
    "testlink_save_profile": save_profile,
    "testlink_delete_profile": delete_profile,
    "testlink_create_testcase": create_testcase,
    "testlink_update_testcase": update_testcase,
    "testlink_upload_report": upload_report,
}


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name not in TOOLS:
        return {"ok": False, "code": 1, "error": {"type": "UnknownTool", "message": f"Unknown tool: {name}"}}
    return TOOLS[name](**(arguments or {}))
