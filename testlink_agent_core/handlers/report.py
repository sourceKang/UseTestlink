from __future__ import annotations

from typing import Any

from ..client import TestLinkClient
from ..errors import TestLinkError, normalize_testlink_error
from ..resolver import NameResolver
from .common import build_row, plan_row, project_row, report_payload_from_args, resolve_report_target, status_counts


def _target_result(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": project_row(target["project"]) if target.get("project") else None,
        "test_plan": plan_row(target["test_plan"]) if isinstance(target.get("test_plan"), dict) else None,
        "build": build_row(target["build"]) if target.get("build") else {"id": target["build_id"]},
        "platform": target.get("platform") or ({"id": target["platform_id"]} if target.get("platform_id") else None),
    }


def report_result(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
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
) -> dict[str, Any]:
    target = resolve_report_target(
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
    payload = report_payload_from_args(item, target)
    result: dict[str, Any] = {
        "mode": "write" if write else "preview",
        "target": _target_result(target),
        "payload": payload,
        "status_counts": status_counts([payload]),
    }
    if write:
        result["response"] = client_obj.report_result(payload)
    return result


def report_results_batch(
    client_obj: TestLinkClient,
    resolver: NameResolver,
    *,
    results: list[dict[str, Any]],
    project: str | None = None,
    plan: str | None = None,
    testplan_id: str | None = None,
    build: str | None = None,
    build_id: str | None = None,
    platform: str | None = None,
    platform_id: str | None = None,
    write: bool = False,
) -> dict[str, Any]:
    if not isinstance(results, list) or not results:
        raise TestLinkError("results must be a non-empty array.")
    target = resolve_report_target(
        resolver,
        project=project,
        plan=plan,
        testplan_id=testplan_id,
        build=build,
        build_id=build_id,
        platform=platform,
        platform_id=platform_id,
    )
    payloads = [report_payload_from_args(item, target) for item in results]
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
        "target": _target_result(target),
        "total_count": len(payloads),
        "status_counts": status_counts(payloads),
        "payloads": payloads,
        "success_count": len(successes),
        "failure_count": len(failures),
        "successes": successes,
        "failures": failures,
    }


def overwrite_result(*, confirm: bool = False) -> dict[str, Any] | None:
    if confirm is True:
        return None
    return {
        "ok": False,
        "code": 1,
        "error": {
            "type": "ConfirmationRequired",
            "message": "overwrite_result requires confirm=true.",
        },
    }


def link_bug(result: dict[str, Any], *, bug_id: str) -> dict[str, Any]:
    if result.get("ok") and isinstance(result.get("result"), dict):
        result["result"]["link_mode"] = "notes"
        result["result"]["bug_id"] = bug_id
        result["result"]["message"] = "Bug ID is written to report_result notes only; native TestLink bugid is not used."
    return result
