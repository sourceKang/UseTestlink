from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from qa_mcp_contracts import CONTRACT_SCHEMA_VERSION, assert_safe_contract, payload_digest, validate_preview_digest
from testlink_agent_core.api import call_tool as legacy_call_tool
from testlink_agent_core.api import report_result as legacy_report_result
from testlink_agent_core.errors import TestLinkError, normalize_testlink_error, redact_secrets
from testlink_agent_core.policy import validate_testcase_row_policy
from testlink_agent_core.testcases import compare_testcase_readback, validate_testcase_steps

from .audit import find_operation_audits, utc_now_iso, write_operation_audit
from .config import DEFAULT_AUDIT_DIR, load_runtime, validate_environment, write_client
from .tools import EXCLUDED_LEGACY_TOOLS, TOOLS


ALLOWED_TOOL_NAMES = {tool["name"] for tool in TOOLS}


def _success(result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "code": 0, "result": redact_secrets(result)}


def _failure(operation_id: str, stage: str, error: BaseException) -> dict[str, Any]:
    normalized = normalize_testlink_error(error)
    return {
        "ok": False,
        "code": 1,
        "error": redact_secrets(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": str(operation_id or "unknown-operation"),
                "error": {
                    "code": str(normalized.code or "TESTLINK_ERROR"),
                    "message": normalized.message,
                    "stage": stage,
                    "retryable": False,
                },
            }
        ),
    }


def _named_target(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TestLinkError(f"Resolved {label} target is missing.")
    target_id = str(value.get("id") or "").strip()
    name = str(value.get("name") or "").strip()
    if not target_id or not name:
        raise TestLinkError(f"Resolved {label} target requires id and name.")
    return {"id": target_id, "name": name}


def _resolve_exact_named(label: str, expected: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [row for row in rows if str(row.get("name") or "").strip().casefold() == expected.strip().casefold()]
    if len(matches) != 1:
        raise TestLinkError(f"Expected exactly one {label} named {expected!r}; found {len(matches)}.")
    return matches[0]


def testlink_resolve_execution_target(
    *,
    operation_id: str,
    environment: str,
    project: str,
    plan: str,
    platform: str,
    build: str,
    testcase_external_id: str | None = None,
    env_file: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    try:
        runtime = load_runtime(env_file=env_file, timeout=timeout)
        selected = validate_environment(environment, runtime.environment)
        client = write_client(runtime)
        project_row = _resolve_exact_named("project", project, client.get_projects())
        plan_row = _resolve_exact_named(
            "plan",
            plan,
            client.get_project_test_plans(str(project_row.get("id") or "")),
        )
        plan_id = str(plan_row.get("id") or "")
        platform_row = _resolve_exact_named("platform", platform, client.get_platforms(plan_id))
        build_row = _resolve_exact_named("build", build, client.get_builds(plan_id))
        target: dict[str, Any] = {
            "project": _named_target(project_row, "project"),
            "plan": _named_target(plan_row, "plan"),
            "platform": _named_target(platform_row, "platform"),
            "build": _named_target(build_row, "build"),
        }
        if testcase_external_id:
            cases = client.get_plan_cases_by_external_id(plan_id, str(platform_row.get("id") or ""))
            matches = [
                case
                for external_id, case in cases.items()
                if external_id.casefold() == testcase_external_id.casefold()
            ]
            if len(matches) != 1:
                raise TestLinkError(
                    f"Testcase is not uniquely present in the selected plan/platform: {testcase_external_id}"
                )
            target["testcase"] = {
                "external_id": testcase_external_id,
                "id": str(matches[0].get("id") or matches[0].get("testcase_id") or ""),
                "name": str(matches[0].get("name") or matches[0].get("testcase_name") or ""),
            }
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "mode": "read-only",
                "target": target,
            }
        )
    except Exception as exc:
        return _failure(operation_id, "resolve-execution-target", exc)


def _build_plan(operation_id: str, environment: str, preview: dict[str, Any]) -> dict[str, Any]:
    target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
    payload = preview.get("payload") if isinstance(preview.get("payload"), dict) else None
    if payload is None:
        raise TestLinkError("TestLink preview did not return a report payload.")
    plan = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": str(operation_id),
        "environment": environment,
        "action": "append-execution",
        "target": {
            "project": _named_target(target.get("project"), "project"),
            "plan": _named_target(target.get("test_plan"), "plan"),
            "platform": _named_target(target.get("platform"), "platform"),
            "build": _named_target(target.get("build"), "build"),
        },
        "payload": payload,
    }
    assert_safe_contract(plan)
    return plan


def _preview_result(plan: dict[str, Any]) -> dict[str, Any]:
    payload = plan["payload"]
    testcase_external_id = str(payload.get("testcaseexternalid") or payload.get("testcaseid") or "")
    if not testcase_external_id:
        raise TestLinkError("Resolved TestLink payload has no testcase identity.")
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": plan["operation_id"],
        "environment": plan["environment"],
        "mode": "preview",
        "preview_digest": payload_digest(plan),
        "planned_write": True,
        "target": plan["target"],
        "execution": {
            "testcase_external_id": testcase_external_id,
            "status": str(payload.get("status") or ""),
            "notes_digest": hashlib.sha256(str(payload.get("notes") or "").encode("utf-8")).hexdigest(),
            **(
                {"duration_minutes": float(payload["execduration"])}
                if payload.get("execduration") not in (None, "")
                else {}
            ),
        },
        "warnings": [],
    }


def _last_execution_row(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if len(value) == 1 and isinstance(next(iter(value.values())), dict):
            return next(iter(value.values()))
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), None)
    return None


def _recover_or_skip_previous_operation(
    *,
    operation_id: str,
    preview_digest: str,
    contract_plan: dict[str, Any],
    runtime: Any,
    audit_dir: str,
) -> dict[str, Any] | None:
    payload = contract_plan["payload"]
    for path, record in find_operation_audits(operation_id, audit_dir):
        if record.get("preview_digest") != preview_digest:
            continue
        if record.get("status") == "success":
            return {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": contract_plan["environment"],
                "preview_digest": preview_digest,
                "status": "skipped-resume",
                "testcase_external_id": _preview_result(contract_plan)["execution"]["testcase_external_id"],
                "execution_id": (
                    str(record.get("execution_id")) if record.get("execution_id") not in (None, "") else None
                ),
                "execution_url": None,
                "audit_id": path.name,
            }
        if record.get("status") == "started":
            client = write_client(runtime)
            latest = _last_execution_row(
                client.get_last_execution_result(
                    testplan_id=str(payload.get("testplanid") or ""),
                    testcase_external_id=str(payload.get("testcaseexternalid") or ""),
                    build_id=str(payload.get("buildid") or ""),
                    platform_id=str(payload.get("platformid") or ""),
                    platform_name=str(payload.get("platformname") or "") or None,
                )
            )
            notes_digest = hashlib.sha256(str((latest or {}).get("notes") or "").encode("utf-8")).hexdigest()
            expected_notes_digest = str(record.get("notes_digest") or "")
            latest_status = str((latest or {}).get("status") or "")
            if latest is not None and notes_digest == expected_notes_digest and latest_status == str(payload.get("status")):
                execution_id = latest.get("id") or latest.get("execution_id")
                recovered = {
                    **record,
                    "status": "success",
                    "recovered": True,
                    "execution_id": execution_id,
                    "finished_at": utc_now_iso(),
                }
                write_operation_audit(recovered, audit_dir, audit_id=path.name)
                return {
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "environment": contract_plan["environment"],
                    "preview_digest": preview_digest,
                    "status": "skipped-resume",
                    "testcase_external_id": _preview_result(contract_plan)["execution"]["testcase_external_id"],
                    "execution_id": str(execution_id) if execution_id is not None else None,
                    "execution_url": None,
                    "audit_id": path.name,
                }
            raise TestLinkError(
                "A previous TestLink operation is still indeterminate; refusing to append a possible duplicate execution."
            )
    return None


def _legacy_preview(**kwargs: Any) -> dict[str, Any]:
    result = legacy_report_result(write=False, **kwargs)
    if not result.get("ok"):
        error = result.get("error") or {}
        raise TestLinkError(str(error.get("message") or error))
    preview = result.get("result")
    if not isinstance(preview, dict):
        raise TestLinkError("TestLink preview returned no structured result.")
    return preview


def _legacy_tool_preview(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = legacy_call_tool(name, {**arguments, "write": False})
    if not result.get("ok"):
        error = result.get("error") or {}
        raise TestLinkError(
            str(error.get("message") or error),
            code=error.get("code"),
            raw=error.get("raw"),
        )
    preview = result.get("result")
    if not isinstance(preview, dict):
        raise TestLinkError("TestLink testcase preview returned no structured result.")
    return preview


def _testcase_request_contract(
    *,
    operation_id: str,
    environment: str,
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    contract = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": operation_id,
        "environment": environment,
        "action": action,
        "arguments": arguments,
    }
    assert_safe_contract(contract)
    return contract


def _build_testcase_plan(
    *,
    operation_id: str,
    environment: str,
    action: str,
    preview: dict[str, Any],
    single_step: bool,
    allow_multi_row: bool,
) -> dict[str, Any]:
    payload = preview.get("payload") if isinstance(preview.get("payload"), dict) else None
    planned_write = payload is not None and not bool(preview.get("duplicate_found"))
    row_validation: dict[str, Any] = {
        "row_policy": validate_testcase_row_policy(
            single_step=single_step,
            allow_multi_row=allow_multi_row,
        ),
        "logical_step_count": 0,
        "planned_row_count": 0,
        "allow_multi_row": allow_multi_row,
    }
    if payload is not None and "steps" in payload:
        row_validation = validate_testcase_steps(
            payload["steps"],
            single_step=single_step,
            allow_multi_row=allow_multi_row,
        )
    plan = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": operation_id,
        "environment": environment,
        "action": action,
        "planned_write": planned_write,
        "target": preview.get("target") or {},
        "row_validation": row_validation,
        "payload": payload,
        "duplicate_found": bool(preview.get("duplicate_found")),
    }
    assert_safe_contract(plan)
    return plan


def _testcase_preview_result(plan: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": plan["operation_id"],
        "environment": plan["environment"],
        "mode": "preview",
        "preview_digest": payload_digest(plan),
        "planned_write": plan["planned_write"],
        "target": plan["target"],
        "row_validation": plan["row_validation"],
        "payload": plan["payload"],
        "duplicate_found": plan["duplicate_found"],
        "duplicates": preview.get("duplicates") or [],
        "message": preview.get("message"),
        "warnings": [],
    }


def _find_identity_value(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            if value.get(key) not in (None, ""):
                return str(value[key])
        for child in value.values():
            found = _find_identity_value(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_identity_value(child, keys)
            if found:
                return found
    return None


def _testcase_readback(
    client: Any,
    *,
    action: str,
    plan: dict[str, Any],
    response: Any = None,
    audit: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, str | None]]:
    payload = plan.get("payload") or {}
    audit = audit or {}
    testcase_id = str(
        audit.get("testcase_id")
        or _find_identity_value(response, ("testcaseid", "testcase_id", "tcase_id", "id"))
        or payload.get("testcaseid")
        or ""
    ).strip()
    external_id = str(
        audit.get("testcase_external_id")
        or _find_identity_value(
            response,
            ("testcaseexternalid", "testcase_external_id", "full_external_id", "full_tc_external_id"),
        )
        or payload.get("testcaseexternalid")
        or ""
    ).strip()
    version_value = audit.get("version") or payload.get("version")
    version = str(version_value).strip() if version_value not in (None, "") else None

    if action == "create-testcase" and not testcase_id and not external_id:
        target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
        suite = target.get("suite") if isinstance(target.get("suite"), dict) else {}
        suite_id = str(suite.get("id") or payload.get("testsuiteid") or "").strip()
        expected_name = str(payload.get("testcasename") or "").strip().casefold()
        matches = [
            item
            for item in client.get_suite_cases(suite_id, deep=False, details="full")
            if isinstance(item, dict)
            and str(item.get("name") or item.get("tcase_name") or "").strip().casefold() == expected_name
        ]
        if len(matches) != 1:
            raise TestLinkError(
                "Could not uniquely resolve the created testcase for readback verification; refusing to report success."
            )
        testcase_id = str(
            matches[0].get("testcaseid")
            or matches[0].get("testcase_id")
            or matches[0].get("tcase_id")
            or matches[0].get("id")
            or ""
        ).strip()
        external_id = str(
            matches[0].get("full_external_id")
            or matches[0].get("full_tc_external_id")
            or matches[0].get("external_id")
            or ""
        ).strip()
        if not version and matches[0].get("version") not in (None, ""):
            version = str(matches[0]["version"])

    if not testcase_id and not external_id:
        raise TestLinkError("Could not resolve a testcase identity for readback verification.")
    readback = client.get_test_case(
        testcase_id=testcase_id or None,
        testcase_external_id=external_id or None,
        version=version,
    )
    return readback, {
        "testcase_id": testcase_id or None,
        "testcase_external_id": external_id or None,
        "version": version,
    }


def _recover_testcase_operation(
    *,
    operation_id: str,
    action: str,
    preview_digest: str,
    request_digest: str,
    runtime: Any,
    audit_dir: str,
) -> dict[str, Any] | None:
    for path, record in find_operation_audits(operation_id, audit_dir, action=action):
        if record.get("preview_digest") != preview_digest:
            continue
        if record.get("request_digest") != request_digest:
            raise TestLinkError("The current testcase request does not match the audited operation inputs.")
        if record.get("status") == "success":
            return {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": record.get("environment"),
                "preview_digest": preview_digest,
                "status": "skipped-resume",
                "verification_status": "verified",
                "testcase_id": record.get("testcase_id"),
                "testcase_external_id": record.get("testcase_external_id"),
                "version": record.get("version"),
                "audit_id": path.name,
            }
        plan = record.get("plan")
        if not isinstance(plan, dict) or not isinstance(plan.get("payload"), dict):
            raise TestLinkError("The prior testcase operation audit cannot safely support resume.")
        validate_preview_digest(plan, preview_digest)
        client = write_client(runtime)
        readback, identity = _testcase_readback(client, action=action, plan=plan, audit=record)
        comparison = compare_testcase_readback(plan["payload"], readback)
        if not comparison["matches"]:
            raise TestLinkError(
                "A prior testcase write is indeterminate and readback does not match; refusing a possible duplicate write.",
                code="TESTCASE_WRITE_INDETERMINATE",
                raw={"audit_id": path.name, "mismatches": comparison["mismatches"]},
            )
        recovered = {
            **record,
            **identity,
            "status": "success",
            "verification_status": "verified",
            "recovered": True,
            "readback_digest": payload_digest(comparison["actual"]),
            "finished_at": utc_now_iso(),
        }
        write_operation_audit(recovered, audit_dir, audit_id=path.name)
        return {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": operation_id,
            "environment": record.get("environment"),
            "preview_digest": preview_digest,
            "status": "skipped-resume",
            "verification_status": "verified",
            **identity,
            "audit_id": path.name,
        }
    return None


def testlink_report_execution(
    *,
    operation_id: str,
    environment: str,
    status: str,
    notes: str,
    testcase_external_id: str | None = None,
    project: str | None = None,
    plan: str | None = None,
    build: str | None = None,
    build_id: str | None = None,
    platform: str | None = None,
    platform_id: str | None = None,
    execution_duration: int | float | None = None,
    write: bool = False,
    preview_digest: str | None = None,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    env_file: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    audit_id: str | None = None
    try:
        runtime = load_runtime(env_file=env_file, timeout=timeout)
        selected = validate_environment(environment, runtime.environment)
        preview_kwargs = {
            "status": status,
            "notes": notes,
            "testcase_external_id": testcase_external_id,
            "project": project,
            "plan": plan,
            "build": build,
            "build_id": build_id,
            "platform": platform,
            "platform_id": platform_id,
            "execution_duration": execution_duration,
            "env_file": env_file,
            "timeout": timeout,
        }
        contract_plan = _build_plan(operation_id, selected, _legacy_preview(**preview_kwargs))
        if not write:
            return _success(_preview_result(contract_plan))
        validate_preview_digest(contract_plan, str(preview_digest or ""))
        previous = _recover_or_skip_previous_operation(
            operation_id=operation_id,
            preview_digest=str(preview_digest),
            contract_plan=contract_plan,
            runtime=runtime,
            audit_dir=audit_dir,
        )
        if previous is not None:
            return _success(previous)
        started = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": "append-execution",
                "status": "started",
                "preview_digest": preview_digest,
                "payload_digest": payload_digest(contract_plan["payload"]),
                "notes_digest": hashlib.sha256(
                    str(contract_plan["payload"].get("notes") or "").encode("utf-8")
                ).hexdigest(),
                "started_at": utc_now_iso(),
            },
            audit_dir,
        )
        audit_id = started.name
        response = write_client(runtime).report_result(contract_plan["payload"])
        execution_id = None
        if isinstance(response, dict):
            execution_id = response.get("execution_id") or response.get("executionId") or response.get("id")
        completed = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": "append-execution",
                "status": "success",
                "preview_digest": preview_digest,
                "testcase_external_id": _preview_result(contract_plan)["execution"]["testcase_external_id"],
                "execution_id": execution_id,
                "response": response,
                "finished_at": utc_now_iso(),
            },
            audit_dir,
            audit_id=audit_id,
        )
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "preview_digest": str(preview_digest),
                "status": "success",
                "testcase_external_id": _preview_result(contract_plan)["execution"]["testcase_external_id"],
                "execution_id": str(execution_id) if execution_id is not None else None,
                "execution_url": None,
                "audit_id": completed.name,
            }
        )
    except Exception as exc:
        if write:
            normalized = normalize_testlink_error(exc)
            try:
                path = write_operation_audit(
                    {
                        "schema_version": CONTRACT_SCHEMA_VERSION,
                        "operation_id": operation_id,
                        "environment": environment,
                        "action": "append-execution",
                        "status": "failed",
                        "preview_digest": preview_digest,
                        "error": normalized.to_dict(),
                        "finished_at": utc_now_iso(),
                    },
                    audit_dir,
                    audit_id=audit_id,
                )
                audit_id = path.name
            except Exception:
                exc = TestLinkError(f"{exc}; additionally, the required TestLink audit could not be written.")
        return _failure(operation_id, "testlink-execution-write" if write else "testlink-execution-preview", exc)


def _protected_testcase_change(
    *,
    operation_id: str,
    environment: str,
    action: str,
    legacy_tool_name: str,
    preview_arguments: dict[str, Any],
    single_step: bool,
    allow_multi_row: bool,
    write: bool,
    preview_digest: str | None,
    audit_dir: str,
    env_file: str | None,
    timeout: int,
) -> dict[str, Any]:
    audit_id: str | None = None
    contract_plan: dict[str, Any] | None = None
    request_digest = ""
    response: Any = None
    write_attempted = False
    try:
        runtime = load_runtime(env_file=env_file, timeout=timeout)
        selected = validate_environment(environment, runtime.environment)
        validate_testcase_row_policy(
            single_step=single_step,
            allow_multi_row=allow_multi_row,
        )
        request_contract = _testcase_request_contract(
            operation_id=operation_id,
            environment=selected,
            action=action,
            arguments=preview_arguments,
        )
        request_digest = payload_digest(request_contract)
        if write and preview_digest:
            previous = _recover_testcase_operation(
                operation_id=operation_id,
                action=action,
                preview_digest=str(preview_digest),
                request_digest=request_digest,
                runtime=runtime,
                audit_dir=audit_dir,
            )
            if previous is not None:
                return _success(previous)

        legacy_preview = _legacy_tool_preview(legacy_tool_name, preview_arguments)
        contract_plan = _build_testcase_plan(
            operation_id=operation_id,
            environment=selected,
            action=action,
            preview=legacy_preview,
            single_step=single_step,
            allow_multi_row=allow_multi_row,
        )
        preview_result = _testcase_preview_result(contract_plan, legacy_preview)
        if not write:
            return _success(preview_result)
        if not contract_plan["planned_write"]:
            raise TestLinkError("The reviewed testcase preview contains no permitted write.")
        validate_preview_digest(contract_plan, str(preview_digest or ""))
        # Repeat immediately before the external write so callers cannot bypass the preview-time check.
        row_validation = contract_plan.get("row_validation") or {}
        if isinstance(contract_plan.get("payload"), dict) and "steps" in contract_plan["payload"]:
            row_validation = validate_testcase_steps(
                contract_plan["payload"]["steps"],
                single_step=single_step,
                allow_multi_row=allow_multi_row,
            )
        contract_plan["row_validation"] = row_validation
        started = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": action,
                "status": "started",
                "preview_digest": preview_digest,
                "request_digest": request_digest,
                "payload_digest": payload_digest(contract_plan["payload"]),
                "plan": contract_plan,
                "started_at": utc_now_iso(),
            },
            audit_dir,
        )
        audit_id = started.name
        client = write_client(runtime)
        write_attempted = True
        if action == "create-testcase":
            response = client.create_test_case(contract_plan["payload"])
        else:
            response = client.update_test_case(contract_plan["payload"])
        written_record = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": operation_id,
            "environment": selected,
            "action": action,
            "status": "written",
            "write_succeeded": True,
            "preview_digest": preview_digest,
            "request_digest": request_digest,
            "plan": contract_plan,
            "response": response,
            "started_at": utc_now_iso(),
        }
        write_operation_audit(written_record, audit_dir, audit_id=audit_id)
        readback, identity = _testcase_readback(
            client,
            action=action,
            plan=contract_plan,
            response=response,
        )
        comparison = compare_testcase_readback(contract_plan["payload"], readback)
        completed_record = {
            **written_record,
            **identity,
            "status": "success" if comparison["matches"] else "verification_failed",
            "verification_status": "verified" if comparison["matches"] else "verification_failed",
            "expected_digest": payload_digest(comparison["expected"]),
            "readback_digest": payload_digest(comparison["actual"]),
            "mismatches": comparison["mismatches"],
            "finished_at": utc_now_iso(),
        }
        completed = write_operation_audit(completed_record, audit_dir, audit_id=audit_id)
        if not comparison["matches"]:
            return _failure(
                operation_id,
                "testcase-readback-verification",
                TestLinkError(
                    "TestLink accepted the testcase write but readback content differs; update is not verified.",
                    code="TESTCASE_READBACK_MISMATCH",
                    raw={"audit_id": completed.name, "mismatches": comparison["mismatches"]},
                ),
            )
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "preview_digest": str(preview_digest),
                "status": "success",
                "verification_status": "verified",
                **identity,
                "row_validation": row_validation,
                "audit_id": completed.name,
            }
        )
    except Exception as exc:
        if write:
            normalized = normalize_testlink_error(exc)
            try:
                status = "indeterminate" if write_attempted else "failed"
                path = write_operation_audit(
                    {
                        "schema_version": CONTRACT_SCHEMA_VERSION,
                        "operation_id": operation_id,
                        "environment": environment,
                        "action": action,
                        "status": status,
                        "write_succeeded": None if write_attempted else False,
                        "preview_digest": preview_digest,
                        "request_digest": request_digest or None,
                        "plan": contract_plan,
                        "response": response,
                        "error": normalized.to_dict(),
                        "finished_at": utc_now_iso(),
                    },
                    audit_dir,
                    audit_id=audit_id,
                )
                audit_id = path.name
            except Exception:
                exc = TestLinkError(f"{exc}; additionally, the required TestLink audit could not be written.")
        return _failure(operation_id, f"{action}-write" if write else f"{action}-preview", exc)


def testlink_create_testcase(
    *,
    operation_id: str,
    environment: str,
    project: str,
    name: str,
    steps: list[str],
    suite_name: str | None = None,
    suite_id: str | None = None,
    author_login: str | None = None,
    summary: str = "",
    preconditions: str = "",
    importance: str = "medium",
    execution_type: str = "manual",
    order: int | None = None,
    single_step: bool = True,
    allow_multi_row: bool = False,
    write: bool = False,
    preview_digest: str | None = None,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    env_file: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    arguments = {
        "project": project,
        "suite_name": suite_name,
        "suite_id": suite_id,
        "name": name,
        "author_login": author_login,
        "summary": summary,
        "preconditions": preconditions,
        "steps": steps,
        "importance": importance,
        "execution_type": execution_type,
        "order": order,
        "single_step": single_step,
        "allow_multi_row": allow_multi_row,
    }
    return _protected_testcase_change(
        operation_id=operation_id,
        environment=environment,
        action="create-testcase",
        legacy_tool_name="create_test_case",
        preview_arguments=arguments,
        single_step=single_step,
        allow_multi_row=allow_multi_row,
        write=write,
        preview_digest=preview_digest,
        audit_dir=audit_dir,
        env_file=env_file,
        timeout=timeout,
    )


def testlink_update_testcase(
    *,
    operation_id: str,
    environment: str,
    testcase_external_id: str | None = None,
    testcase_id: str | None = None,
    version: str | None = None,
    name: str | None = None,
    summary: str | None = None,
    preconditions: str | None = None,
    steps: list[str] | None = None,
    importance: str | None = None,
    execution_type: str | None = None,
    single_step: bool = True,
    allow_multi_row: bool = False,
    write: bool = False,
    preview_digest: str | None = None,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    env_file: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    arguments = {
        "testcase_external_id": testcase_external_id,
        "testcase_id": testcase_id,
        "version": version,
        "name": name,
        "summary": summary,
        "preconditions": preconditions,
        "steps": steps,
        "importance": importance,
        "execution_type": execution_type,
        "single_step": single_step,
        "allow_multi_row": allow_multi_row,
    }
    return _protected_testcase_change(
        operation_id=operation_id,
        environment=environment,
        action="update-testcase",
        legacy_tool_name="update_test_case",
        preview_arguments=arguments,
        single_step=single_step,
        allow_multi_row=allow_multi_row,
        write=write,
        preview_digest=preview_digest,
        audit_dir=audit_dir,
        env_file=env_file,
        timeout=timeout,
    )


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name == "testlink_resolve_execution_target":
        try:
            return testlink_resolve_execution_target(**(arguments or {}))
        except TypeError as exc:
            operation_id = str((arguments or {}).get("operation_id") or "unknown-operation")
            return _failure(operation_id, "arguments", exc)
    if name == "testlink_report_execution":
        try:
            return testlink_report_execution(**(arguments or {}))
        except TypeError as exc:
            operation_id = str((arguments or {}).get("operation_id") or "unknown-operation")
            return _failure(operation_id, "arguments", exc)
    if name in {"testlink_create_testcase", "testlink_update_testcase"}:
        try:
            handler = testlink_create_testcase if name == "testlink_create_testcase" else testlink_update_testcase
            return handler(**(arguments or {}))
        except TypeError as exc:
            operation_id = str((arguments or {}).get("operation_id") or "unknown-operation")
            return _failure(operation_id, "arguments", exc)
    if name not in ALLOWED_TOOL_NAMES or name in EXCLUDED_LEGACY_TOOLS:
        return {"ok": False, "code": 1, "error": {"type": "UnknownTool", "message": f"Unknown tool: {name}"}}
    return legacy_call_tool(name, arguments)
