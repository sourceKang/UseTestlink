from __future__ import annotations

from collections import Counter
from typing import Any

from ..errors import TestLinkError
from ..resolver import NameResolver
from ..testcases import normalize_testcase


def project_row(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": project.get("id"),
        "name": project.get("name"),
        "prefix": project.get("prefix"),
        "active": project.get("active"),
        "is_public": project.get("is_public"),
    }


def plan_row(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": plan.get("id"),
        "name": plan.get("name"),
        "active": plan.get("active"),
        "is_public": plan.get("is_public"),
        "testproject_id": plan.get("testproject_id"),
    }


def suite_row(suite: dict[str, Any] | None) -> dict[str, Any] | None:
    if suite is None:
        return None
    return {
        "id": suite.get("id"),
        "name": suite.get("name"),
        "parent_id": suite.get("parent_id"),
        "path": suite.get("path"),
        "depth": suite.get("depth"),
        "node_order": suite.get("node_order"),
    }


def build_row(build: dict[str, Any] | None) -> dict[str, Any] | None:
    if build is None:
        return None
    return {
        "id": build.get("id"),
        "name": build.get("name"),
        "active": build.get("active"),
        "is_open": build.get("is_open"),
        "release_date": build.get("release_date"),
        "closed_on_date": build.get("closed_on_date"),
        "creation_ts": build.get("creation_ts"),
    }


def case_row(case: dict[str, Any]) -> dict[str, Any]:
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


def require_one(label: str, values: dict[str, Any]) -> None:
    present = [name for name, value in values.items() if value not in (None, "")]
    if len(present) != 1:
        options = ", ".join(values)
        raise TestLinkError(f"Exactly one {label} is required: {options}.")


def coerce_bool_flag(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value).strip().casefold()
    if text in ("1", "true", "yes", "y", "open", "active"):
        return 1
    if text in ("0", "false", "no", "n", "closed", "inactive"):
        return 0
    raise TestLinkError(f"Boolean flag must be true or false, got: {value}")


def compose_notes(
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


def status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(item.get("status") or "") for item in items)
    return {
        "pass": counter.get("p", 0),
        "fail": counter.get("f", 0),
        "blocked": counter.get("b", 0),
    }


def resolve_report_target(
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


def report_payload_from_args(item: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    require_one(
        "testcase identifier",
        {
            "testcase_id": item.get("testcase_id"),
            "testcase_external_id": item.get("testcase_external_id"),
        },
    )
    status = str(item.get("status") or "").strip().lower()
    if status not in {"p", "f", "b"}:
        raise TestLinkError("status must be one of: p, f, b.")
    notes = compose_notes(
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
        overwrite = coerce_bool_flag(item["overwrite"])
        if overwrite and item.get("confirm_overwrite") is not True:
            raise TestLinkError("overwrite=true requires confirm_overwrite=true.")
        payload["overwrite"] = overwrite
    return payload
