from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import mimetypes
import xmlrpc.client
from collections import Counter
from pathlib import Path
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
from .suites import collect_test_suites
from .testcases import create_testcase_payload, flatten_plan_cases, normalize_testcase, update_testcase_payload


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


def _project_row(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": project.get("id"),
        "name": project.get("name"),
        "prefix": project.get("prefix"),
        "active": project.get("active"),
        "is_public": project.get("is_public"),
    }


def _plan_row(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": plan.get("id"),
        "name": plan.get("name"),
        "active": plan.get("active"),
        "is_public": plan.get("is_public"),
        "testproject_id": plan.get("testproject_id"),
    }


def _suite_row(suite: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": suite.get("id"),
        "name": suite.get("name"),
        "parent_id": suite.get("parent_id"),
        "path": suite.get("path"),
        "depth": suite.get("depth"),
        "node_order": suite.get("node_order"),
    }


def _build_row(build: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": build.get("id"),
        "name": build.get("name"),
        "active": build.get("active"),
        "is_open": build.get("is_open"),
        "release_date": build.get("release_date"),
        "closed_on_date": build.get("closed_on_date"),
        "creation_ts": build.get("creation_ts"),
    }


def _case_row(case: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_testcase(case)
    return {
        "external_id": normalized.get("external_id"),
        "testcase_id": normalized.get("testcase_id"),
        "version": normalized.get("version"),
        "name": normalized.get("name"),
        "execution_order": normalized.get("execution_order"),
        "platform_id": normalized.get("platform_id"),
        "raw": case,
    }


def _require_one(label: str, values: dict[str, Any]) -> None:
    present = [name for name, value in values.items() if value not in (None, "")]
    if len(present) != 1:
        options = ", ".join(values)
        raise TestLinkError(f"Exactly one {label} is required: {options}.")


def _coerce_bool_flag(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value).strip().casefold()
    if text in ("1", "true", "yes", "y", "open", "active"):
        return 1
    if text in ("0", "false", "no", "n", "closed", "inactive"):
        return 0
    raise TestLinkError(f"Boolean flag must be true or false, got: {value}")


def _compose_notes(
    *,
    notes: str,
    framework: str | None = None,
    executed_at: str | None = None,
    failure_summary: str | None = None,
    bug_id: str | None = None,
) -> str:
    lines: list[str] = []
    if framework:
        lines.append(f"Framework: {framework}")
    if executed_at:
        lines.append(f"Executed at: {executed_at}")
    if failure_summary:
        lines.append(f"Failure summary: {failure_summary}")
    if bug_id:
        lines.append(f"BUG-ID: {bug_id}")
    if notes:
        if lines:
            lines.append("")
        lines.append(notes)
    return "\n".join(lines)


def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(item.get("status") or "") for item in items)
    return {
        "pass": counter.get("p", 0),
        "fail": counter.get("f", 0),
        "blocked": counter.get("b", 0),
    }


def _resolve_report_target(
    resolver: NameResolver,
    *,
    project: str | None,
    plan: str | None,
    testplan_id: str | None,
    build: str | None,
    build_id: str | None,
    platform: str | None,
    platform_id: str | None,
) -> dict[str, Any]:
    if not testplan_id and (not project or not plan):
        raise TestLinkError("Use testplan_id, or project plus plan.")
    resolved_project = resolver.resolve_project(project) if project else None
    resolved_plan = resolver.resolve_test_plan(project, plan) if project and plan else {"id": testplan_id}
    plan_id = str(resolved_plan["id"])

    resolved_build = None
    resolved_build_id = build_id
    if build:
        resolved_build = resolver.resolve_build(plan_id, build)
        resolved_build_id = str(resolved_build["id"])
    if not resolved_build_id:
        raise TestLinkError("build_id or build is required.")

    resolved_platform = None
    resolved_platform_id = platform_id
    if platform:
        resolved_platform = resolver.resolve_platform(plan_id, platform)
        resolved_platform_id = str(resolved_platform["id"])

    return {
        "project": resolved_project,
        "test_plan": resolved_plan,
        "testplan_id": plan_id,
        "build": resolved_build,
        "build_id": resolved_build_id,
        "platform": resolved_platform,
        "platform_id": resolved_platform_id,
    }


def _report_payload_from_args(item: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    _require_one(
        "testcase identifier",
        {
            "testcase_id": item.get("testcase_id"),
            "testcase_external_id": item.get("testcase_external_id"),
        },
    )
    status = str(item.get("status") or "").strip().lower()
    if status not in {"p", "f", "b"}:
        raise TestLinkError("status must be one of: p, f, b.")
    notes = _compose_notes(
        notes=str(item.get("notes") or ""),
        framework=item.get("framework") or item.get("framework_name"),
        executed_at=item.get("executed_at") or item.get("execution_time"),
        failure_summary=item.get("failure_summary"),
        bug_id=item.get("bug_id"),
    )
    payload: dict[str, Any] = {
        "testplanid": target["testplan_id"],
        "buildid": target["build_id"],
        "status": status,
        "notes": notes,
    }
    if item.get("testcase_id"):
        payload["testcaseid"] = item["testcase_id"]
    else:
        payload["testcaseexternalid"] = item["testcase_external_id"]
    platform_name = item.get("platformname") or item.get("platform") or item.get("platform_name")
    if platform_name:
        payload["platformname"] = platform_name
    elif target.get("platform"):
        payload["platformname"] = target["platform"].get("name")
    if target.get("platform_id"):
        payload["platformid"] = target["platform_id"]
    if item.get("execution_duration") not in (None, ""):
        payload["execduration"] = item["execution_duration"]
    if item.get("overwrite") not in (None, ""):
        overwrite = _coerce_bool_flag(item["overwrite"])
        if overwrite and item.get("confirm_overwrite") is not True:
            raise TestLinkError("overwrite=true requires confirm_overwrite=true.")
        payload["overwrite"] = overwrite
    return payload


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
    def run(_client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        project = resolver.resolve_project(name)
        return {"project": _project_row(project)}

    return _run_live(run, **kwargs)


def find_test_plan(project: str, name: str, **kwargs: Any) -> dict[str, Any]:
    def run(_client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        resolved_project = resolver.resolve_project(project)
        plan = resolver.resolve_test_plan(project, name)
        return {"project": _project_row(resolved_project), "test_plan": _plan_row(plan)}

    return _run_live(run, **kwargs)


def get_builds(project: str, plan: str, open_only: bool = False, **kwargs: Any) -> dict[str, Any]:
    def run(_client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        resolved_project = resolver.resolve_project(project)
        resolved_plan = resolver.resolve_test_plan(project, plan)
        rows = [_build_row(build) for build in resolver.get_builds(str(resolved_plan["id"]))]
        if open_only:
            rows = [row for row in rows if str(row.get("active")) == "1" and str(row.get("is_open")) == "1"]
        rows = sorted(rows, key=lambda row: str(row.get("creation_ts") or ""), reverse=True)
        return {"project": _project_row(resolved_project), "test_plan": _plan_row(resolved_plan), "builds": rows}

    return _run_live(run, **kwargs)


def list_test_suites(
    project: str,
    parent_suite_id: str | None = None,
    parent_suite_name: str | None = None,
    recursive: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    def run(client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        resolved_project = resolver.resolve_project(project)
        resolved_parent_id = parent_suite_id
        if parent_suite_name:
            resolved_parent_id = str(resolver.resolve_suite(project, parent_suite_name)["id"])
        suites = collect_test_suites(
            client_obj,
            str(resolved_project["id"]),
            parent_suite_id=resolved_parent_id,
            recursive=recursive,
        )
        rows = [_suite_row(suite) for suite in suites]
        return {
            "project": _project_row(resolved_project),
            "parent_suite_id": resolved_parent_id,
            "recursive": recursive,
            "suite_count": len(rows),
            "suites": rows,
        }

    return _run_live(run, **kwargs)


def list_test_cases(
    project: str,
    suite_name: str | None = None,
    suite_id: str | None = None,
    recursive: bool = True,
    details: str = "full",
    **kwargs: Any,
) -> dict[str, Any]:
    def run(client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        resolved_project = resolver.resolve_project(project)
        resolved_suite: dict[str, Any] | None = None
        resolved_suite_id = suite_id
        if suite_name:
            resolved_suite = resolver.resolve_suite(project, suite_name)
            resolved_suite_id = str(resolved_suite["id"])
        if not resolved_suite_id:
            raise TestLinkError("suite_id or suite_name is required.")
        raw_cases = client_obj.get_suite_cases(str(resolved_suite_id), deep=recursive, details=details)
        rows = [_case_row(case) for case in flatten_plan_cases(raw_cases) or raw_cases]
        return {
            "project": _project_row(resolved_project),
            "suite": _suite_row(resolved_suite) if resolved_suite else {"id": resolved_suite_id},
            "recursive": recursive,
            "details": details,
            "case_count": len(rows),
            "test_cases": rows,
        }

    return _run_live(run, **kwargs)


def get_test_case(
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    version: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    def run(client_obj: TestLinkClient, _resolver: NameResolver) -> dict[str, Any]:
        testcase = client_obj.get_test_case(
            testcase_id=testcase_id,
            testcase_external_id=testcase_external_id,
            version=version,
        )
        return {
            "query": {
                "testcase_id": testcase_id,
                "testcase_external_id": testcase_external_id,
                "version": version,
            },
            "test_case": testcase,
        }

    return _run_live(run, **kwargs)


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
    def run(client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        resolved_project = resolver.resolve_project(project)
        resolved_plan = resolver.resolve_test_plan(project, plan)
        resolved_build_id = build_id
        resolved_build: dict[str, Any] | None = None
        if build:
            resolved_build = resolver.resolve_build(str(resolved_plan["id"]), build)
            resolved_build_id = str(resolved_build["id"])
        resolved_platform_id = platform_id
        resolved_platform: dict[str, Any] | None = None
        if platform:
            resolved_platform = resolver.resolve_platform(str(resolved_plan["id"]), platform)
            resolved_platform_id = str(resolved_platform["id"])
        result = client_obj.get_last_execution_result(
            testplan_id=str(resolved_plan["id"]),
            testcase_id=testcase_id,
            testcase_external_id=testcase_external_id,
            build_id=resolved_build_id,
            platform_id=resolved_platform_id,
            platform_name=platform,
        )
        return {
            "project": _project_row(resolved_project),
            "test_plan": _plan_row(resolved_plan),
            "build": _build_row(resolved_build) if resolved_build else ({"id": resolved_build_id} if resolved_build_id else None),
            "platform": resolved_platform if resolved_platform else ({"id": resolved_platform_id} if resolved_platform_id else None),
            "testcase_id": testcase_id,
            "testcase_external_id": testcase_external_id,
            "last_result": result,
        }

    return _run_live(run, **kwargs)


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
    def run(client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        target = _resolve_report_target(
            resolver,
            project=project,
            plan=plan,
            testplan_id=testplan_id,
            build=build,
            build_id=build_id,
            platform=platform,
            platform_id=platform_id,
        )
        item = {
            "testcase_external_id": testcase_external_id,
            "testcase_id": testcase_id,
            "status": status,
            "notes": notes,
            "platformname": platformname or platform,
            "framework": framework,
            "framework_name": framework_name,
            "executed_at": executed_at,
            "execution_time": execution_time,
            "failure_summary": failure_summary,
            "bug_id": bug_id,
            "execution_duration": execution_duration,
            "overwrite": overwrite,
            "confirm_overwrite": confirm_overwrite,
        }
        payload = _report_payload_from_args(item, target)
        result: dict[str, Any] = {
            "mode": "write" if write else "preview",
            "target": {
                "project": _project_row(target["project"]) if target.get("project") else None,
                "test_plan": _plan_row(target["test_plan"]) if isinstance(target.get("test_plan"), dict) else None,
                "build": _build_row(target["build"]) if target.get("build") else {"id": target["build_id"]},
                "platform": target.get("platform") or ({"id": target["platform_id"]} if target.get("platform_id") else None),
            },
            "payload": payload,
            "status_counts": _status_counts([payload]),
        }
        if write:
            result["response"] = client_obj.report_result(payload)
        return result

    return _run_live(run, **kwargs)


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
    def run(client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        if not isinstance(results, list) or not results:
            raise TestLinkError("results must be a non-empty array.")
        target = _resolve_report_target(
            resolver,
            project=project,
            plan=plan,
            testplan_id=testplan_id,
            build=build,
            build_id=build_id,
            platform=platform,
            platform_id=platform_id,
        )
        payloads = [_report_payload_from_args(item, target) for item in results]
        successes: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        if write:
            for index, payload in enumerate(payloads):
                try:
                    response = client_obj.report_result(payload)
                    successes.append({"index": index, "status": payload["status"], "payload": payload, "response": response})
                except Exception as exc:
                    normalized = normalize_testlink_error(exc)
                    failures.append({"index": index, "status": payload["status"], "payload": payload, "error": normalized.to_dict()})
        return {
            "mode": "write" if write else "preview",
            "target": {
                "project": _project_row(target["project"]) if target.get("project") else None,
                "test_plan": _plan_row(target["test_plan"]) if isinstance(target.get("test_plan"), dict) else None,
                "build": _build_row(target["build"]) if target.get("build") else {"id": target["build_id"]},
                "platform": target.get("platform") or ({"id": target["platform_id"]} if target.get("platform_id") else None),
            },
            "total_count": len(payloads),
            "status_counts": _status_counts(payloads),
            "payloads": payloads,
            "success_count": len(successes),
            "failure_count": len(failures),
            "successes": successes,
            "failures": failures,
        }

    return _run_live(run, **kwargs)


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
    def run(client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        resolved_project = resolver.resolve_project(project)
        resolved_plan = resolver.resolve_test_plan(project, plan)
        builds = resolver.get_builds(str(resolved_plan["id"]))
        duplicates = [build for build in builds if str(build.get("name") or "").casefold() == name.casefold()]
        payload: dict[str, Any] = {
            "testplanid": str(resolved_plan["id"]),
            "buildname": name,
            "buildnotes": notes,
            "active": _coerce_bool_flag(active),
            "open": _coerce_bool_flag(open),
        }
        if releasedate:
            payload["releasedate"] = releasedate
        result: dict[str, Any] = {
            "mode": "write" if write else "preview",
            "target": {"project": _project_row(resolved_project), "test_plan": _plan_row(resolved_plan)},
            "payload": payload,
            "duplicate_found": bool(duplicates),
            "duplicates": [_build_row(build) for build in duplicates],
        }
        if duplicates:
            result["message"] = "Build already exists. Use the existing build instead of creating a duplicate."
            return result
        if write:
            result["response"] = client_obj.create_build(payload)
        return result

    return _run_live(run, **kwargs)


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
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    def run(client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        if not name:
            raise TestLinkError("name is required.")
        resolved_project = resolver.resolve_project(project)
        resolved_suite: dict[str, Any] | None = None
        resolved_suite_id = suite_id
        if suite_name:
            resolved_suite = resolver.resolve_suite(project, suite_name)
            resolved_suite_id = str(resolved_suite["id"])
        if not resolved_suite_id:
            raise TestLinkError("suite_id or suite_name is required.")
        existing = client_obj.get_suite_cases(str(resolved_suite_id), deep=False, details="simple")
        duplicates = [case for case in existing if str(case.get("name") or case.get("tcase_name") or "").casefold() == name.casefold()]
        if duplicates:
            return {
                "mode": "write" if write else "preview",
                "target": {
                    "project": _project_row(resolved_project),
                    "suite": _suite_row(resolved_suite) if resolved_suite else {"id": resolved_suite_id},
                },
                "duplicate_found": True,
                "duplicates": [_case_row(case) for case in duplicates],
                "message": "Test case already exists in this suite. Use the existing case instead of creating a duplicate.",
            }
        args = _args(
            project=project,
            suite_id=resolved_suite_id,
            name=name,
            author_login=author_login,
            summary=summary,
            preconditions=preconditions,
            steps=steps or [],
            importance=importance,
            execution_type=execution_type,
            order=order,
            duplicate_action="block",
        )
        payload = create_testcase_payload(args, resolved_project, suite_id=str(resolved_suite_id))
        result: dict[str, Any] = {
            "mode": "write" if write else "preview",
            "target": {
                "project": _project_row(resolved_project),
                "suite": _suite_row(resolved_suite) if resolved_suite else {"id": resolved_suite_id},
            },
            "payload": payload,
            "duplicate_found": False,
            "duplicates": [],
        }
        if write:
            result["response"] = client_obj.create_test_case(payload)
        return result

    return _run_live(run, **kwargs)


def update_test_case(
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    version: str | None = None,
    name: str | None = None,
    summary: str | None = None,
    preconditions: str | None = None,
    steps: list[str] | None = None,
    importance: str | None = None,
    execution_type: str | None = None,
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    def run(client_obj: TestLinkClient, _resolver: NameResolver) -> dict[str, Any]:
        args = _args(
            testcase_external_id=testcase_external_id,
            testcase_id=testcase_id,
            version=version,
            name=name,
            summary=summary,
            preconditions=preconditions,
            steps=steps,
            importance=importance,
            execution_type=execution_type,
        )
        payload = update_testcase_payload(args)
        result: dict[str, Any] = {
            "mode": "write" if write else "preview",
            "payload": payload,
        }
        if write:
            result["response"] = client_obj.update_test_case(payload)
        return result

    return _run_live(run, **kwargs)


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
    def run(client_obj: TestLinkClient, resolver: NameResolver) -> dict[str, Any]:
        _require_one("testcase identifier", {"testcase_external_id": testcase_external_id, "testcase_id": testcase_id})
        resolved_project = resolver.resolve_project(project)
        resolved_plan = resolver.resolve_test_plan(project, plan)
        resolved_platform = None
        resolved_platform_id = platform_id
        if platform:
            resolved_platform = resolver.resolve_platform(str(resolved_plan["id"]), platform)
            resolved_platform_id = str(resolved_platform["id"])

        case = client_obj.get_test_case(testcase_id=testcase_id, testcase_external_id=testcase_external_id, version=version)
        case_items = case if isinstance(case, list) else [case]
        case_dict = next((item for item in case_items if isinstance(item, dict)), {})
        external_id = testcase_external_id or str(case_dict.get("full_tc_external_id") or case_dict.get("full_external_id") or case_dict.get("external_id") or "")
        resolved_version = version or str(case_dict.get("version") or "")
        if not external_id:
            raise TestLinkError("Could not resolve testcase external ID for add_case_to_plan.")
        if not resolved_version:
            raise TestLinkError("version is required when TestLink cannot resolve it from the testcase.")

        raw_plan_cases = client_obj.get_plan_cases(str(resolved_plan["id"]), resolved_platform_id, details="simple")
        plan_cases = flatten_plan_cases(raw_plan_cases)
        duplicates = [
            item
            for item in plan_cases
            if str(item.get("full_external_id") or item.get("external_id") or item.get("tc_external_id") or "") == external_id
        ]
        payload: dict[str, Any] = {
            "testprojectid": str(resolved_project["id"]),
            "testplanid": str(resolved_plan["id"]),
            "testcaseexternalid": external_id,
            "version": int(resolved_version) if str(resolved_version).isdigit() else resolved_version,
        }
        if resolved_platform_id:
            payload["platformid"] = resolved_platform_id
        if execution_order is not None:
            payload["executionorder"] = execution_order
        if urgency not in (None, ""):
            payload["urgency"] = int(urgency) if str(urgency).isdigit() else urgency
        result: dict[str, Any] = {
            "mode": "write" if write else "preview",
            "target": {
                "project": _project_row(resolved_project),
                "test_plan": _plan_row(resolved_plan),
                "platform": resolved_platform or ({"id": resolved_platform_id} if resolved_platform_id else None),
            },
            "payload": payload,
            "already_in_plan": bool(duplicates),
            "matches": [_case_row(item) for item in duplicates],
        }
        if duplicates:
            result["message"] = "Test case is already in this plan/platform."
            return result
        if write:
            result["response"] = client_obj.add_test_case_to_plan(payload)
        return result

    return _run_live(run, **kwargs)


def upload_attachment(
    attachment_type: str,
    target_id: str,
    file: str,
    title: str | None = None,
    description: str = "",
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    def run(client_obj: TestLinkClient, _resolver: NameResolver) -> dict[str, Any]:
        path = Path(file)
        if not path.exists() or not path.is_file():
            raise TestLinkError(f"Attachment file does not exist: {path}")
        filename = path.name
        filetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        payload: dict[str, Any] = {
            "filename": filename,
            "filetype": filetype,
            "content": content,
            "title": title or filename,
            "description": description,
        }
        attachment_type_key = attachment_type.strip().casefold()
        uploaders: dict[str, tuple[str, str]] = {
            "testcase": ("testcaseid", "upload_test_case_attachment"),
            "testsuite": ("testsuiteid", "upload_test_suite_attachment"),
            "testproject": ("testprojectid", "upload_test_project_attachment"),
            "execution": ("executionid", "upload_execution_attachment"),
        }
        if attachment_type_key not in uploaders:
            raise TestLinkError("attachment_type must be one of: testcase, testsuite, testproject, execution.")
        id_field, uploader_name = uploaders[attachment_type_key]
        payload[id_field] = target_id
        preview_payload = {key: value for key, value in payload.items() if key != "content"}
        preview_payload["content_base64_bytes"] = len(content.encode("ascii"))
        result: dict[str, Any] = {
            "mode": "write" if write else "preview",
            "attachment_type": attachment_type_key,
            "payload": preview_payload,
        }
        if write:
            uploader = getattr(client_obj, uploader_name)
            result["response"] = uploader(payload)
        return result

    return _run_live(run, **kwargs)


def overwrite_result(confirm: bool = False, **kwargs: Any) -> dict[str, Any]:
    if confirm is not True:
        return {
            "ok": False,
            "code": 1,
            "error": {
                "type": "ConfirmationRequired",
                "message": "overwrite_result requires confirm=true.",
            },
        }
    kwargs["overwrite"] = True
    kwargs["confirm_overwrite"] = True
    return report_result(**kwargs)


def delete_execution(
    execution_id: str,
    confirm: bool = False,
    write: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    def run(client_obj: TestLinkClient, _resolver: NameResolver) -> dict[str, Any]:
        result: dict[str, Any] = {
            "mode": "write" if write else "preview",
            "execution_id": execution_id,
            "requires_confirmation": True,
            "confirmed": confirm is True,
        }
        if write and confirm is not True:
            raise TestLinkError("delete_execution requires confirm=true when write=true.")
        if write:
            result["response"] = client_obj.delete_execution(execution_id)
        return result

    return _run_live(run, **kwargs)


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
    result = report_result(**kwargs)
    if result.get("ok") and isinstance(result.get("result"), dict):
        result["result"]["link_mode"] = "notes"
        result["result"]["bug_id"] = bug_id
        result["result"]["message"] = "Bug ID is written to report_result notes only; native TestLink bugid is not used."
    return result
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




