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
from . import __version__
from .client import TestLinkClient
from .config import load_testlink_settings
from .errors import TestLinkError, normalize_testlink_error, redact_secrets
from .resolver import NameResolver
from .handlers import mutate as mutate_handlers
from .handlers import query as query_handlers
from .handlers import report as report_handlers


CommandHandler = Callable[[argparse.Namespace], int]

_BASE_DEFAULTS: dict[str, Any] = {
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
    "redmine_template": None,
    "redmine_custom_fields": None,
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
    "single_step": False,
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
        normalized = normalize_testlink_error(fault)
        return {"ok": False, "code": 2, "error": normalized.to_dict()}
    except TestLinkError as exc:
        return {"ok": False, "code": 1, "error": exc.to_dict()}
    except Exception as exc:
        normalized = normalize_testlink_error(exc)
        return {
            "ok": False,
            "code": 1,
            "error": normalized.to_dict(),
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
    return redact_secrets(payload)


def _client(env_file: str | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> TestLinkClient:
    settings = load_testlink_settings(env_file=env_file, timeout=timeout)
    client = TestLinkClient(settings.url, settings.devkey, timeout=settings.timeout)
    if not client.check_devkey():
        raise TestLinkError("tl.checkDevKey failed.")
    return client


def _run_live(callback: Callable[[TestLinkClient, NameResolver], dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    try:
        client = _client(kwargs.get("env_file"), int(kwargs.get("timeout") or DEFAULT_TIMEOUT_SECONDS))
        result = callback(client, NameResolver(client))
        return {"ok": True, "code": 0, "result": redact_secrets(result)}
    except Exception as exc:
        normalized = normalize_testlink_error(exc)
        return {"ok": False, "code": 1, "error": normalized.to_dict()}



def about(**kwargs: Any) -> dict[str, Any]:
    from .commands import parse_common_env

    args = _args(**kwargs)
    result: dict[str, Any] = {
        "server": "testlink-mcp",
        "version": __version__,
    }
    try:
        client = parse_common_env(args)
        result["testlink_about"] = client.about()
        result["check_devkey"] = True
    except Exception as exc:
        normalized = normalize_testlink_error(exc)
        return {"ok": False, "code": 1, "result": result, "error": normalized.to_dict()}
    return {"ok": True, "code": 0, "result": redact_secrets(result)}


def find_project(name: str, **kwargs: Any) -> dict[str, Any]:
    return _run_live(lambda client_obj, resolver: query_handlers.find_project(client_obj, resolver, name=name), **kwargs)


def find_test_plan(project: str, name: str, **kwargs: Any) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: query_handlers.find_test_plan(client_obj, resolver, project=project, name=name),
        **kwargs,
    )


def get_builds(project: str, plan: str, open_only: bool = False, **kwargs: Any) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: query_handlers.get_builds(
            client_obj,
            resolver,
            project=project,
            plan=plan,
            open_only=open_only,
        ),
        **kwargs,
    )


def list_test_suites(
    project: str,
    parent_suite_id: str | None = None,
    parent_suite_name: str | None = None,
    recursive: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: query_handlers.list_test_suites(
            client_obj,
            resolver,
            project=project,
            parent_suite_id=parent_suite_id,
            parent_suite_name=parent_suite_name,
            recursive=recursive,
        ),
        **kwargs,
    )


def list_test_cases(
    project: str,
    suite_name: str | None = None,
    suite_id: str | None = None,
    recursive: bool = True,
    details: str = "full",
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: query_handlers.list_test_cases(
            client_obj,
            resolver,
            project=project,
            suite_name=suite_name,
            suite_id=suite_id,
            recursive=recursive,
            details=details,
        ),
        **kwargs,
    )


def get_test_case(
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    version: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: query_handlers.get_test_case(
            client_obj,
            resolver,
            testcase_external_id=testcase_external_id,
            testcase_id=testcase_id,
            version=version,
        ),
        **kwargs,
    )


def get_last_result(
    project: str,
    plan: str,
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    build: str | None = None,
    build_id: str | None = None,
    platform: str | None = None,
    platform_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: query_handlers.get_last_result(
            client_obj,
            resolver,
            project=project,
            plan=plan,
            testcase_external_id=testcase_external_id,
            testcase_id=testcase_id,
            build=build,
            build_id=build_id,
            platform=platform,
            platform_id=platform_id,
        ),
        **kwargs,
    )


def report_result(
    status: str,
    notes: str,
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    project: str | None = None,
    plan: str | None = None,
    testplan_id: str | None = None,
    build: str | None = None,
    build_id: str | None = None,
    platform: str | None = None,
    platform_id: str | None = None,
    platformname: str | None = None,
    framework: str | None = None,
    framework_name: str | None = None,
    executed_at: str | None = None,
    execution_time: str | None = None,
    failure_summary: str | None = None,
    bug_id: str | None = None,
    execution_duration: int | float | None = None,
    overwrite: bool = False,
    confirm_overwrite: bool = False,
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: report_handlers.report_result(
            client_obj,
            resolver,
            status=status,
            notes=notes,
            testcase_external_id=testcase_external_id,
            testcase_id=testcase_id,
            project=project,
            plan=plan,
            testplan_id=testplan_id,
            build=build,
            build_id=build_id,
            platform=platform,
            platform_id=platform_id,
            platformname=platformname,
            framework=framework,
            framework_name=framework_name,
            executed_at=executed_at,
            execution_time=execution_time,
            failure_summary=failure_summary,
            bug_id=bug_id,
            execution_duration=execution_duration,
            overwrite=overwrite,
            confirm_overwrite=confirm_overwrite,
            write=write,
        ),
        **kwargs,
    )


def report_results_batch(
    results: list[dict[str, Any]],
    project: str | None = None,
    plan: str | None = None,
    testplan_id: str | None = None,
    build: str | None = None,
    build_id: str | None = None,
    platform: str | None = None,
    platform_id: str | None = None,
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: report_handlers.report_results_batch(
            client_obj,
            resolver,
            results=results,
            project=project,
            plan=plan,
            testplan_id=testplan_id,
            build=build,
            build_id=build_id,
            platform=platform,
            platform_id=platform_id,
            write=write,
        ),
        **kwargs,
    )


def create_build(
    project: str,
    plan: str,
    name: str,
    notes: str = "",
    active: bool = True,
    open: bool = True,
    releasedate: str | None = None,
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: mutate_handlers.create_build(
            client_obj,
            resolver,
            project=project,
            plan=plan,
            name=name,
            notes=notes,
            active=active,
            open=open,
            releasedate=releasedate,
            write=write,
        ),
        **kwargs,
    )


def create_test_case(
    project: str,
    suite_name: str | None = None,
    suite_id: str | None = None,
    name: str | None = None,
    author_login: str | None = None,
    summary: str = "",
    preconditions: str = "",
    steps: list[str] | None = None,
    importance: str = "medium",
    execution_type: str = "manual",
    order: int | None = None,
    single_step: bool = False,
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: mutate_handlers.create_test_case(
            client_obj,
            resolver,
            args_builder=_args,
            project=project,
            suite_name=suite_name,
            suite_id=suite_id,
            name=name,
            author_login=author_login,
            summary=summary,
            preconditions=preconditions,
            steps=steps,
            importance=importance,
            execution_type=execution_type,
            order=order,
            single_step=single_step,
            write=write,
        ),
        **kwargs,
    )


def update_test_case(
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    version: str | None = None,
    name: str | None = None,
    summary: str | None = None,
    preconditions: str | None = None,
    steps: list[str] | None = None,
    single_step: bool = False,
    importance: str | None = None,
    execution_type: str | None = None,
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: mutate_handlers.update_test_case(
            client_obj,
            resolver,
            args_builder=_args,
            testcase_external_id=testcase_external_id,
            testcase_id=testcase_id,
            version=version,
            name=name,
            summary=summary,
            preconditions=preconditions,
            steps=steps,
            single_step=single_step,
            importance=importance,
            execution_type=execution_type,
            write=write,
        ),
        **kwargs,
    )


def add_case_to_plan(
    project: str,
    plan: str,
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    version: str | None = None,
    platform: str | None = None,
    platform_id: str | None = None,
    execution_order: int | None = None,
    urgency: str | int | None = None,
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: mutate_handlers.add_case_to_plan(
            client_obj,
            resolver,
            project=project,
            plan=plan,
            testcase_external_id=testcase_external_id,
            testcase_id=testcase_id,
            version=version,
            platform=platform,
            platform_id=platform_id,
            execution_order=execution_order,
            urgency=urgency,
            write=write,
        ),
        **kwargs,
    )


def upload_attachment(
    attachment_type: str,
    target_id: str,
    file: str,
    title: str | None = None,
    description: str = "",
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: mutate_handlers.upload_attachment(
            client_obj,
            resolver,
            attachment_type=attachment_type,
            target_id=target_id,
            file=file,
            title=title,
            description=description,
            write=write,
        ),
        **kwargs,
    )


def overwrite_result(confirm: bool = False, **kwargs: Any) -> dict[str, Any]:
    confirmation_error = report_handlers.overwrite_result(confirm=confirm)
    if confirmation_error is not None:
        return confirmation_error
    kwargs["overwrite"] = True
    kwargs["confirm_overwrite"] = True
    return report_result(**kwargs)


def delete_execution(
    execution_id: str,
    confirm: bool = False,
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    return _run_live(
        lambda client_obj, resolver: mutate_handlers.delete_execution(
            client_obj,
            resolver,
            execution_id=execution_id,
            confirm=confirm,
            write=write,
        ),
        **kwargs,
    )


def link_bug(
    bug_id: str,
    status: str,
    notes: str = "",
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    if not bug_id:
        return {
            "ok": False,
            "code": 1,
            "error": {"type": "ValidationError", "message": "bug_id is required."},
        }
    kwargs["bug_id"] = bug_id
    kwargs["status"] = status
    kwargs["notes"] = notes
    kwargs["write"] = write
    return report_handlers.link_bug(report_result(**kwargs), bug_id=bug_id)

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
    "find_project": find_project,
    "find_test_plan": find_test_plan,
    "list_test_suites": list_test_suites,
    "list_test_cases": list_test_cases,
    "get_test_case": get_test_case,
    "get_last_result": get_last_result,
    "get_builds": get_builds,
    "report_result": report_result,
    "report_results_batch": report_results_batch,
    "create_build": create_build,
    "create_test_case": create_test_case,
    "update_test_case": update_test_case,
    "add_case_to_plan": add_case_to_plan,
    "upload_attachment": upload_attachment,
    "overwrite_result": overwrite_result,
    "delete_execution": delete_execution,
    "link_bug": link_bug,
    "testlink_about": about,
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




