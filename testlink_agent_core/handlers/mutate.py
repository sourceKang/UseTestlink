from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Callable

from ..client import TestLinkClient
from ..errors import TestLinkError
from ..resolver import NameResolver
from ..testcases import create_testcase_payload, flatten_plan_cases, update_testcase_payload
from .common import build_row, case_row, coerce_bool_flag, plan_row, project_row, require_one, suite_row

ArgsBuilder = Callable[..., Any]


def create_build(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    project: str,
    plan: str,
    name: str,
    notes: str = "",
    active: bool = True,
    open: bool = True,
    releasedate: str | None = None,
    write: bool = False,
) -> dict[str, Any]:
    resolved_project = resolver.resolve_project(project)
    resolved_plan = resolver.resolve_test_plan(project, plan)
    builds = resolver.get_builds(str(resolved_plan["id"]))
    duplicates = [build for build in builds if str(build.get("name") or "").casefold() == name.casefold()]
    payload: dict[str, Any] = {
        "testplanid": str(resolved_plan["id"]),
        "buildname": name,
        "buildnotes": notes,
        "active": coerce_bool_flag(active),
        "open": coerce_bool_flag(open),
    }
    if releasedate:
        payload["releasedate"] = releasedate
    result: dict[str, Any] = {
        "mode": "write" if write else "preview",
        "target": {"project": project_row(resolved_project), "test_plan": plan_row(resolved_plan)},
        "payload": payload,
        "duplicate_found": bool(duplicates),
        "duplicates": [row for row in (build_row(build) for build in duplicates) if row is not None],
    }
    if duplicates:
        result["message"] = "Build already exists. Use the existing build instead of creating a duplicate."
        return result
    if write:
        result["response"] = client_obj.create_build(payload)
    return result


def create_test_case(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    args_builder: ArgsBuilder,
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
) -> dict[str, Any]:
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
    target = {
        "project": project_row(resolved_project),
        "suite": suite_row(resolved_suite) if resolved_suite else {"id": resolved_suite_id},
    }
    if duplicates:
        return {
            "mode": "write" if write else "preview",
            "target": target,
            "duplicate_found": True,
            "duplicates": [case_row(case) for case in duplicates],
            "message": "Test case already exists in this suite. Use the existing case instead of creating a duplicate.",
        }
    args = args_builder(
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
        single_step=single_step,
        duplicate_action="block",
    )
    payload = create_testcase_payload(args, resolved_project, suite_id=str(resolved_suite_id))
    result: dict[str, Any] = {
        "mode": "write" if write else "preview",
        "target": target,
        "payload": payload,
        "duplicate_found": False,
        "duplicates": [],
    }
    if write:
        result["response"] = client_obj.create_test_case(payload)
    return result


def update_test_case(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    args_builder: ArgsBuilder,
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
) -> dict[str, Any]:
    args = args_builder(
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
    )
    payload = update_testcase_payload(args)
    result: dict[str, Any] = {
        "mode": "write" if write else "preview",
        "payload": payload,
    }
    if write:
        result["response"] = client_obj.update_test_case(payload)
    return result


def add_case_to_plan(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
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
) -> dict[str, Any]:
    require_one("testcase identifier", {"testcase_external_id": testcase_external_id, "testcase_id": testcase_id})
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
    external_id = testcase_external_id or str(
        case_dict.get("full_tc_external_id") or case_dict.get("full_external_id") or case_dict.get("external_id") or ""
    )
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
            "project": project_row(resolved_project),
            "test_plan": plan_row(resolved_plan),
            "platform": resolved_platform or ({"id": resolved_platform_id} if resolved_platform_id else None),
        },
        "payload": payload,
        "already_in_plan": bool(duplicates),
        "matches": [case_row(item) for item in duplicates],
    }
    if duplicates:
        result["message"] = "Test case is already in this plan/platform."
        return result
    if write:
        result["response"] = client_obj.add_test_case_to_plan(payload)
    return result


def upload_attachment(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    attachment_type: str,
    target_id: str,
    file: str,
    title: str | None = None,
    description: str = "",
    write: bool = False,
) -> dict[str, Any]:
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


def delete_execution(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    execution_id: str,
    confirm: bool = False,
    write: bool = False,
) -> dict[str, Any]:
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
