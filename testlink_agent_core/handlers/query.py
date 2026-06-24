from __future__ import annotations

from typing import Any

from ..client import TestLinkClient
from ..errors import TestLinkError
from ..resolver import NameResolver
from ..suites import collect_test_suites
from ..testcases import flatten_plan_cases
from .common import build_row, case_row, plan_row, project_row, suite_row


def find_project(client_obj: TestLinkClient, resolver: NameResolver, *, name: str) -> dict[str, Any]:
    project = resolver.resolve_project(name)
    return {"project": project_row(project)}


def find_test_plan(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    project: str,
    name: str,
) -> dict[str, Any]:
    resolved_project = resolver.resolve_project(project)
    plan = resolver.resolve_test_plan(project, name)
    return {"project": project_row(resolved_project), "test_plan": plan_row(plan)}


def get_builds(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    project: str,
    plan: str,
    open_only: bool = False,
) -> dict[str, Any]:
    resolved_project = resolver.resolve_project(project)
    resolved_plan = resolver.resolve_test_plan(project, plan)
    rows = [build_row(build) for build in resolver.get_builds(str(resolved_plan["id"]))]
    rows = [row for row in rows if row is not None]
    if open_only:
        rows = [row for row in rows if str(row.get("active")) == "1" and str(row.get("is_open")) == "1"]
    rows = sorted(rows, key=lambda row: str(row.get("creation_ts") or ""), reverse=True)
    return {"project": project_row(resolved_project), "test_plan": plan_row(resolved_plan), "builds": rows}


def list_test_suites(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    project: str,
    parent_suite_id: str | None = None,
    parent_suite_name: str | None = None,
    recursive: bool = True,
) -> dict[str, Any]:
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
    rows = [suite_row(suite) for suite in suites]
    rows = [row for row in rows if row is not None]
    return {
        "project": project_row(resolved_project),
        "parent_suite_id": resolved_parent_id,
        "recursive": recursive,
        "suite_count": len(rows),
        "suites": rows,
    }


def list_test_cases(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    project: str,
    suite_name: str | None = None,
    suite_id: str | None = None,
    recursive: bool = True,
    details: str = "full",
) -> dict[str, Any]:
    resolved_project = resolver.resolve_project(project)
    resolved_suite: dict[str, Any] | None = None
    resolved_suite_id = suite_id
    if suite_name:
        resolved_suite = resolver.resolve_suite(project, suite_name)
        resolved_suite_id = str(resolved_suite["id"])
    if not resolved_suite_id:
        raise TestLinkError("suite_id or suite_name is required.")
    raw_cases = client_obj.get_suite_cases(str(resolved_suite_id), deep=recursive, details=details)
    rows = [case_row(case) for case in flatten_plan_cases(raw_cases) or raw_cases]
    return {
        "project": project_row(resolved_project),
        "suite": suite_row(resolved_suite) if resolved_suite else {"id": resolved_suite_id},
        "recursive": recursive,
        "details": details,
        "case_count": len(rows),
        "test_cases": rows,
    }


def get_test_case(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
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


def get_last_result(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    project: str,
    plan: str,
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    build: str | None = None,
    build_id: str | None = None,
    platform: str | None = None,
    platform_id: str | None = None,
) -> dict[str, Any]:
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
        "project": project_row(resolved_project),
        "test_plan": plan_row(resolved_plan),
        "build": build_row(resolved_build) if resolved_build else ({"id": resolved_build_id} if resolved_build_id else None),
        "platform": resolved_platform if resolved_platform else ({"id": resolved_platform_id} if resolved_platform_id else None),
        "testcase_id": testcase_id,
        "testcase_external_id": testcase_external_id,
        "last_result": result,
    }
