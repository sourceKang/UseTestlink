from __future__ import annotations

import hashlib
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

from qa_mcp_contracts import CONTRACT_SCHEMA_VERSION, assert_safe_contract, payload_digest, validate_preview_digest
from testlink_agent_core.api import call_tool as legacy_call_tool
from testlink_agent_core.api import report_result as legacy_report_result
from testlink_agent_core.errors import TestLinkError, normalize_testlink_error, redact_secrets
from testlink_agent_core.handlers import report as report_handlers
from testlink_agent_core.policy import validate_testcase_row_policy
from testlink_agent_core.resolver import NameResolver
from testlink_agent_core.testcases import compare_testcase_readback, validate_testcase_steps

from . import custom_api
from .audit import find_operation_audits, utc_now_iso, write_operation_audit
from .config import DEFAULT_AUDIT_DIR, load_runtime, validate_environment, write_client
from .tools import EXCLUDED_LEGACY_TOOLS, TOOLS


ALLOWED_TOOL_NAMES = {tool["name"] for tool in TOOLS}

# Set only by qa-integration-agent for the short-lived child it keeps open for
# one preview or execute batch. The child then authenticates once and resolves
# project/plan/platform/build names once, instead of once per testcase. A
# long-running testlink-mcp started by a user never sets it, so it never serves
# a stale target.
BATCH_SESSION_ENV = "TESTLINK_MCP_BATCH_SESSION"
_batch_session: dict[str, Any] = {}


def batch_session_enabled() -> bool:
    return os.environ.get(BATCH_SESSION_ENV, "").strip() == "1"


def _session_connection(runtime: Any) -> tuple[Any, NameResolver]:
    """Return this batch child's authenticated client and name resolver.

    The key covers the endpoint, credential digest, timeout and environment, so
    a changed env file never reuses a connection made with other credentials.
    """
    settings = runtime.settings
    key = (
        str(settings.url),
        hashlib.sha256(str(settings.devkey).encode("utf-8")).hexdigest(),
        int(settings.timeout),
        str(runtime.environment),
    )
    if _batch_session.get("key") != key:
        client = write_client(runtime)
        _batch_session.clear()
        _batch_session.update({"key": key, "client": client, "resolver": NameResolver(client)})
    return _batch_session["client"], _batch_session["resolver"]


def _execution_client(runtime: Any) -> Any:
    if batch_session_enabled():
        return _session_connection(runtime)[0]
    return write_client(runtime)


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


def _execution_outcome(response: Any) -> tuple[str | None, str | None]:
    """Return (execution_id, rejection_reason) for a reportTCResult response.

    TestLink's XML-RPC answers with a list of structs, for example
    [{"status": true, "operation": "reportTCResult", "message": "Success!", "id": 3043053}],
    so reading the id off the response as if it were a bare mapping silently
    loses it and records a successful write with execution_id null. The same
    envelope carries rejections: an error struct has a "code", and a test plan
    whose platform is missing or ambiguous is answered with the platform list
    instead of a result, which must not be recorded as a successful execution.
    A bare mapping is still accepted for deployments and fakes that return one.
    """
    records = response if isinstance(response, list) else [response]
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("code") is not None or record.get("status") is False:
            reason = record.get("message") or record.get("code")
            return None, str(reason or "TestLink rejected the execution.")
        execution_id = record.get("execution_id") or record.get("executionId") or record.get("id")
        if execution_id not in (None, ""):
            return str(execution_id), None
    return None, "the response carried no execution id."


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


def _resolve_exact_target(
    client: Any,
    *,
    project: str,
    plan: str,
    platform: str,
    build: str,
    testcase_external_id: str | None = None,
) -> dict[str, Any]:
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
        target["testcase"] = _plan_case_identity(testcase_external_id, matches[0])
    return target


def _plan_case_identity(external_id: str, case: dict[str, Any]) -> dict[str, str]:
    """Read the testcase identity from a tl.getTestCasesForTestPlan row.

    TestLink 1.9 rows carry tcase_id, tcase_name and tcversion_id (also with details="simple");
    the generic keys remain as fallbacks for other shapes.
    """

    def first(*keys: str) -> str:
        for key in keys:
            value = case.get(key)
            if value not in (None, "") and str(value) != "None":
                return str(value)
        return ""

    return {
        "external_id": external_id,
        "id": first("tcase_id", "testcase_id", "tc_id", "id"),
        "name": first("tcase_name", "testcase_name", "name"),
        "version_id": first("tcversion_id"),
    }


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
        target = _resolve_exact_target(
            client,
            project=project,
            plan=plan,
            platform=platform,
            build=build,
            testcase_external_id=testcase_external_id,
        )
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
            client = _execution_client(runtime)
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


def _legacy_preview(*, runtime: Any = None, **kwargs: Any) -> dict[str, Any]:
    if runtime is not None and batch_session_enabled():
        client, resolver = _session_connection(runtime)
        arguments = {key: value for key, value in kwargs.items() if key not in {"env_file", "timeout"}}
        return redact_secrets(report_handlers.report_result(client, resolver, write=False, **arguments))
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
        contract_plan = _build_plan(operation_id, selected, _legacy_preview(runtime=runtime, **preview_kwargs))
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
        response = _execution_client(runtime).report_result(contract_plan["payload"])
        execution_id, rejection = _execution_outcome(response)
        if rejection is not None:
            raise TestLinkError(f"TestLink did not record the execution: {rejection}")
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


LINK_BUG_ACTION = "link-bug"
LINK_BUG_STATUSES = ("p", "f", "b")
MAX_LINK_BUG_IDS = 20
DEFAULT_LINK_EXECUTION_DURATION = 5
# A "_<n>" suffix makes the bug-link endpoint report only step n, so the ID must end with "-<number>".
_LINK_EXTERNAL_ID_RE = re.compile(r"^[^\s,]+-\d+$")
_LINK_BUG_ID_RE = re.compile(r"^[1-9]\d{0,9}$")
LINK_BUG_SIDE_EFFECTS = (
    "Appends one TestLink execution with the given status for this testcase/build/platform; "
    "the bug-link endpoint has no link-only mode.",
    "The first link of a Redmine bug in this build makes TestLink add the Redmine note "
    "\"The bug is still exist on <project> <build>, please double check the issue\" to that issue, "
    "even when the issue is Resolved or Closed; the note does not change the issue's updated_on.",
    "TestLink does not confirm the link: bug IDs unknown to the plan's issue tracker are skipped silently.",
)
# testlink-mcp holds no Redmine credentials, so the caller checks Redmine before writing.
LINK_BUG_PRE_WRITE_CHECKS = (
    "Read each bug's Redmine status (redmine-mcp redmine_get_issue); tell the user when an issue is "
    "Resolved or Closed, because it will still receive the automatic note.",
    "Confirm the testcase name and version match the intended testcase.",
)


def _normalize_link_bug_ids(bug_ids: Any) -> list[str]:
    if not isinstance(bug_ids, (list, tuple)):
        raise TestLinkError("bug_ids must be a list of numeric Redmine issue IDs.", code="INVALID_ARGUMENT")
    normalized: list[str] = []
    for value in bug_ids:
        text = str(value).strip()
        if not _LINK_BUG_ID_RE.fullmatch(text):
            raise TestLinkError(f"Bug ID must be numeric: {text!r}", code="INVALID_ARGUMENT")
        if text not in normalized:
            normalized.append(text)
    if not normalized:
        raise TestLinkError("At least one bug ID is required.", code="INVALID_ARGUMENT")
    if len(normalized) > MAX_LINK_BUG_IDS:
        raise TestLinkError(f"At most {MAX_LINK_BUG_IDS} bug IDs can be linked at once.", code="INVALID_ARGUMENT")
    return normalized


def _build_link_bug_plan(
    *,
    operation_id: str,
    environment: str,
    target: dict[str, Any],
    base_url: str,
    endpoint: str,
    tester_id: str,
    bug_ids: list[str],
    status: str,
    notes: str,
    execution_duration: int,
) -> dict[str, Any]:
    testcase = target.get("testcase") if isinstance(target.get("testcase"), dict) else {}
    external_id = str(testcase.get("external_id") or "").strip()
    if not external_id:
        raise TestLinkError("Resolved link target has no testcase.")
    plan = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": str(operation_id),
        "environment": environment,
        "action": LINK_BUG_ACTION,
        "target": target,
        "request": {
            "endpoint": f"{base_url}/{endpoint}",
            "params": {
                "testplan_id": target["plan"]["id"],
                "build_id": target["build"]["id"],
                "platform_id": target["platform"]["id"],
                "tester_id": tester_id,
                "tc_id": external_id,
                "status": status,
                "issue_id": ",".join(bug_ids),
                "notes": notes,
                "execution_duration": str(execution_duration),
            },
        },
    }
    assert_safe_contract(plan)
    return plan


def _link_bug_preview_result(plan: dict[str, Any]) -> dict[str, Any]:
    params = plan["request"]["params"]
    warnings: list[str] = []
    if params["status"] != "f":
        warnings.append(
            f"Status {params['status']!r} becomes the latest execution result of this testcase; "
            "bugs are normally linked with a Fail ('f')."
        )
    testcase = plan["target"].get("testcase") or {}
    if not testcase.get("id") or not testcase.get("name"):
        warnings.append(
            "TestLink returned no testcase id/name for this external ID; verify the testcase on the "
            "TestLink page before writing."
        )
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": plan["operation_id"],
        "environment": plan["environment"],
        "mode": "preview",
        "preview_digest": payload_digest(plan),
        "planned_write": True,
        "target": plan["target"],
        "link": {
            "testcase_external_id": params["tc_id"],
            "bug_ids": params["issue_id"].split(","),
            "status": params["status"],
            "tester_id": params["tester_id"],
            "notes": params["notes"],
            "execution_duration_minutes": params["execution_duration"],
            "endpoint": plan["request"]["endpoint"],
        },
        "side_effects": list(LINK_BUG_SIDE_EFFECTS),
        "pre_write_checks": list(LINK_BUG_PRE_WRITE_CHECKS),
        "warnings": warnings,
    }


def _previous_link_bug_operation(
    *,
    operation_id: str,
    preview_digest: str,
    environment: str,
    audit_dir: str,
) -> dict[str, Any] | None:
    """Skip an already submitted request; fail closed on any unresolved or reused operation.

    The endpoint cannot be read back, so an unresolved attempt blocks every later write of the
    same operation_id, whatever its digest: retrying could append a second execution.
    """
    submitted: tuple[Path, dict[str, Any]] | None = None
    for path, record in find_operation_audits(operation_id, audit_dir, action=LINK_BUG_ACTION):
        if record.get("status") in {"started", "indeterminate"}:
            raise TestLinkError(
                "A previous bug link request for this operation may have reached TestLink; check the testcase's "
                "bug list on the TestLink page before retrying with a new operation_id.",
                code="LINK_BUG_INDETERMINATE",
                raw={"audit_id": path.name},
            )
        if record.get("status") == "submitted":
            if record.get("preview_digest") != preview_digest:
                raise TestLinkError(
                    "This operation_id already submitted a different bug link; use a new operation_id.",
                    code="LINK_BUG_OPERATION_REUSED",
                    raw={"audit_id": path.name},
                )
            submitted = submitted or (path, record)
    if submitted is None:
        return None
    path, record = submitted
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": operation_id,
        "environment": environment,
        "preview_digest": preview_digest,
        "status": "skipped-resume",
        "verification_status": "link_unverified",
        "testcase_external_id": record.get("testcase_external_id"),
        "bug_ids": record.get("bug_ids"),
        "audit_id": path.name,
    }


def _link_execution_readback(client: Any, params: dict[str, str]) -> dict[str, Any]:
    """Read the latest execution back; the link itself cannot be read through any known API."""
    try:
        latest = _last_execution_row(
            client.get_last_execution_result(
                testplan_id=params["testplan_id"],
                testcase_external_id=params["tc_id"],
                build_id=params["build_id"],
                platform_id=params["platform_id"],
            )
        )
    except Exception as exc:
        return {"checked": False, "error": normalize_testlink_error(exc).message}
    if latest is None:
        return {"checked": True, "found": False, "matches": False}
    status = str(latest.get("status") or "")
    tester_id = str(latest.get("tester_id") or "")
    return {
        "checked": True,
        "found": True,
        "execution_id": str(latest.get("id") or latest.get("execution_id") or "") or None,
        "status": status,
        "tester_id": tester_id,
        "execution_ts": latest.get("execution_ts"),
        "matches": status == params["status"] and tester_id == params["tester_id"],
    }


def testlink_link_bug(
    *,
    operation_id: str,
    environment: str,
    project: str,
    plan: str,
    platform: str,
    build: str,
    testcase_external_id: str,
    bug_ids: list[str],
    status: str,
    notes: str = "",
    execution_duration: int = DEFAULT_LINK_EXECUTION_DURATION,
    write: bool = False,
    preview_digest: str | None = None,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    env_file: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    # The server does not enforce the input schema, so only a real boolean true may write.
    write = write is True
    audit_id: str | None = None
    contract_plan: dict[str, Any] | None = None
    write_attempted = False
    response_body: str | None = None
    try:
        runtime = load_runtime(env_file=env_file, timeout=timeout)
        selected = validate_environment(environment, runtime.environment)
        tester_id = custom_api.configured_tester_id()
        endpoint = custom_api.configured_link_endpoint()
        base_url = custom_api.web_base_url(runtime.settings.url)
        external_id = str(testcase_external_id or "").strip()
        if not _LINK_EXTERNAL_ID_RE.fullmatch(external_id):
            raise TestLinkError(
                "testcase_external_id must be a plain external ID such as PRJ-1234 (no step suffix).",
                code="INVALID_ARGUMENT",
            )
        if status not in LINK_BUG_STATUSES:
            raise TestLinkError("status must be one of p, f, b.", code="INVALID_ARGUMENT")
        if isinstance(execution_duration, bool) or not isinstance(execution_duration, int) or execution_duration < 0:
            raise TestLinkError("execution_duration must be a non-negative integer.", code="INVALID_ARGUMENT")
        normalized_bugs = _normalize_link_bug_ids(bug_ids)
        client = write_client(runtime)
        target = _resolve_exact_target(
            client,
            project=project,
            plan=plan,
            platform=platform,
            build=build,
            testcase_external_id=external_id,
        )
        contract_plan = _build_link_bug_plan(
            operation_id=operation_id,
            environment=selected,
            target=target,
            base_url=base_url,
            endpoint=endpoint,
            tester_id=tester_id,
            bug_ids=normalized_bugs,
            status=status,
            notes=str(notes or ""),
            execution_duration=execution_duration,
        )
        if not write:
            return _success(_link_bug_preview_result(contract_plan))
        validate_preview_digest(contract_plan, str(preview_digest or ""))
        previous = _previous_link_bug_operation(
            operation_id=operation_id,
            preview_digest=str(preview_digest),
            environment=selected,
            audit_dir=audit_dir,
        )
        if previous is not None:
            return _success(previous)
        params = contract_plan["request"]["params"]
        started = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": LINK_BUG_ACTION,
                "status": "started",
                "preview_digest": preview_digest,
                "testcase_external_id": params["tc_id"],
                "bug_ids": normalized_bugs,
                "plan": contract_plan,
                "started_at": utc_now_iso(),
            },
            audit_dir,
        )
        audit_id = started.name
        write_attempted = True
        response_body = custom_api.set_result(base_url, endpoint, params, timeout=runtime.settings.timeout)
        if not custom_api.response_reached(response_body):
            raise TestLinkError(
                "The bug-link endpoint did not echo the request; the link outcome is unknown.",
                code="LINK_BUG_NOT_ECHOED",
            )
        readback = _link_execution_readback(client, params)
        warnings: list[str] = []
        if not readback.get("matches"):
            warnings.append(
                "The latest execution read back does not match the submitted status/tester_id (or could not be "
                "read); the request may not have been applied. Check the testcase on the TestLink page."
            )
        completed = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": LINK_BUG_ACTION,
                "status": "submitted",
                "verification_status": "link_unverified",
                "preview_digest": preview_digest,
                "testcase_external_id": params["tc_id"],
                "bug_ids": normalized_bugs,
                "plan": contract_plan,
                "response_excerpt": custom_api.response_excerpt(response_body),
                "execution_readback": readback,
                "warnings": warnings,
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
                "status": "submitted",
                "verification_status": "link_unverified",
                "testcase_external_id": params["tc_id"],
                "bug_ids": normalized_bugs,
                "execution_readback": readback,
                "message": (
                    "Link request reached TestLink, which does not confirm links; "
                    "check the testcase's bug list on the TestLink page."
                ),
                "warnings": warnings,
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
                        "action": LINK_BUG_ACTION,
                        "status": "indeterminate" if write_attempted else "failed",
                        "preview_digest": preview_digest,
                        "plan": contract_plan,
                        "response_excerpt": (
                            custom_api.response_excerpt(response_body) if response_body is not None else None
                        ),
                        "error": normalized.to_dict(),
                        "finished_at": utc_now_iso(),
                    },
                    audit_dir,
                    audit_id=audit_id,
                )
                audit_id = path.name
            except Exception:
                exc = TestLinkError(f"{exc}; additionally, the required TestLink audit could not be written.")
        return _failure(operation_id, "testlink-link-bug-write" if write else "testlink-link-bug-preview", exc)


MAX_LATEST_RESULT_CASES = 1000
_EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9]+-\d+$")
NOT_RUN_STATUS = "n"


def _external_id_sort_key(external_id: str) -> tuple[str, int]:
    prefix, _, number = external_id.rpartition("-")
    return prefix.casefold(), int(number) if number.isdigit() else 0


def _latest_result_row(external_id: str, value: Any) -> dict[str, Any]:
    row = _last_execution_row(value)
    status = str((row or {}).get("status") or "")
    execution_id = str((row or {}).get("id") or (row or {}).get("execution_id") or "")
    # TestLink answers a never-run testcase with {"id": -1} and no status.
    if not status or execution_id in ("", "-1"):
        return {"testcase_external_id": external_id, "status": NOT_RUN_STATUS, "execution_id": None}
    return {
        "testcase_external_id": external_id,
        "status": status,
        "execution_id": execution_id,
        "execution_ts": (row or {}).get("execution_ts"),
        "tester_id": str((row or {}).get("tester_id") or "") or None,
    }


def testlink_get_latest_results(
    *,
    operation_id: str,
    environment: str,
    project: str,
    plan: str,
    platform: str,
    build: str,
    testcase_external_ids: list[str] | None = None,
    all_in_plan: bool = False,
    expected_status: str | None = None,
    env_file: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Read the latest execution of many testcases on one exact build/platform in one call."""
    try:
        runtime = load_runtime(env_file=env_file, timeout=timeout)
        selected = validate_environment(environment, runtime.environment)
        if not isinstance(all_in_plan, bool):
            raise TestLinkError("all_in_plan must be true or false.", code="INVALID_ARGUMENT")
        if all_in_plan == (testcase_external_ids is not None):
            raise TestLinkError(
                "Pass either testcase_external_ids or all_in_plan=true, not both or neither.",
                code="INVALID_ARGUMENT",
            )
        if expected_status is not None and expected_status not in LINK_BUG_STATUSES:
            raise TestLinkError("expected_status must be one of p, f, b.", code="INVALID_ARGUMENT")
        requested: list[str] = []
        if testcase_external_ids is not None:
            if not isinstance(testcase_external_ids, (list, tuple)) or not testcase_external_ids:
                raise TestLinkError("testcase_external_ids must be a non-empty list.", code="INVALID_ARGUMENT")
            for value in testcase_external_ids:
                text = str(value).strip()
                if not _EXTERNAL_ID_RE.fullmatch(text):
                    raise TestLinkError(f"Not a plain external testcase ID: {text!r}", code="INVALID_ARGUMENT")
                if text.casefold() not in {item.casefold() for item in requested}:
                    requested.append(text)
            if len(requested) > MAX_LATEST_RESULT_CASES:
                raise TestLinkError(
                    f"At most {MAX_LATEST_RESULT_CASES} testcases can be read at once.", code="INVALID_ARGUMENT"
                )
        client = write_client(runtime)
        target = _resolve_exact_target(client, project=project, plan=plan, platform=platform, build=build)
        plan_id = target["plan"]["id"]
        platform_id = target["platform"]["id"]
        build_id = target["build"]["id"]
        plan_cases = {
            external_id.casefold(): external_id
            for external_id in client.get_plan_cases_by_external_id(plan_id, platform_id)
        }
        if all_in_plan:
            requested = sorted(plan_cases.values(), key=_external_id_sort_key)
            if len(requested) > MAX_LATEST_RESULT_CASES:
                raise TestLinkError(
                    f"The plan/platform has {len(requested)} testcases; pass at most "
                    f"{MAX_LATEST_RESULT_CASES} testcase_external_ids instead.",
                    code="INVALID_ARGUMENT",
                )
        rows: list[dict[str, Any]] = []
        not_in_plan: list[str] = []
        errors: list[dict[str, str]] = []
        for external_id in requested:
            plan_external_id = plan_cases.get(external_id.casefold())
            if plan_external_id is None:
                not_in_plan.append(external_id)
                continue
            try:
                result = client.get_last_execution_result(
                    testplan_id=plan_id,
                    testcase_external_id=plan_external_id,
                    build_id=build_id,
                    platform_id=platform_id,
                )
            except Exception as exc:
                errors.append(
                    {"testcase_external_id": plan_external_id, "message": normalize_testlink_error(exc).message}
                )
                continue
            rows.append(_latest_result_row(plan_external_id, result))
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        listed = rows if expected_status is None else [row for row in rows if row["status"] != expected_status]
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "mode": "read-only",
                "target": target,
                "requested": len(requested),
                "status_counts": dict(sorted(counts.items())),
                "expected_status": expected_status,
                "all_match_expected": (
                    expected_status is not None and not listed and not not_in_plan and not errors
                ),
                "rows": listed,
                "rows_omitted": len(rows) - len(listed),
                "not_in_plan": not_in_plan,
                "errors": errors,
            }
        )
    except Exception as exc:
        return _failure(operation_id, "latest-results", exc)


CREATE_BUILD_ACTION = "create-build"


def _build_name_key(name: Any) -> str:
    # TestLink build names are stored as typed, sometimes with stray or doubled spaces.
    return " ".join(str(name or "").split()).casefold()


def _matching_builds(builds: Any, name: str) -> list[dict[str, str]]:
    key = _build_name_key(name)
    return [
        {"id": str(build.get("id") or ""), "name": str(build.get("name") or "")}
        for build in (builds if isinstance(builds, list) else [])
        if isinstance(build, dict) and _build_name_key(build.get("name")) == key
    ]


def _created_id(response: Any) -> tuple[str | None, str | None]:
    """Return (id, rejection) from a TestLink XML-RPC create response.

    An error struct is an explicit rejection. A response without either leaves the
    outcome to readback instead of being treated as success or failure.
    """
    records = response if isinstance(response, list) else [response]
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("code") is not None or record.get("status") is False:
            return None, str(record.get("message") or record.get("code") or "TestLink rejected the request.")
        created = record.get("id")
        if created not in (None, ""):
            return str(created), None
    return None, None


def _validate_build_inputs(name: Any, release_date: Any, active: Any, open_: Any) -> str:
    build_name = str(name or "")
    if not build_name.strip():
        raise TestLinkError("Build name is required.", code="INVALID_ARGUMENT")
    if build_name != build_name.strip():
        raise TestLinkError("Build name must not start or end with spaces.", code="INVALID_ARGUMENT")
    if release_date not in (None, ""):
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(release_date)):
                raise ValueError
            date.fromisoformat(str(release_date))
        except ValueError:
            raise TestLinkError("release_date must be a valid YYYY-MM-DD date.", code="INVALID_ARGUMENT") from None
    for label, value in (("active", active), ("open", open_)):
        if not isinstance(value, bool):
            raise TestLinkError(f"{label} must be true or false.", code="INVALID_ARGUMENT")
    return build_name


def _build_create_plan(
    *,
    operation_id: str,
    environment: str,
    project_row: dict[str, Any],
    plan_row: dict[str, Any],
    name: str,
    notes: str,
    release_date: str | None,
    active: bool,
    open_: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "testplanid": str(plan_row.get("id") or ""),
        "buildname": name,
        "buildnotes": notes,
        "active": 1 if active else 0,
        "open": 1 if open_ else 0,
    }
    if release_date:
        payload["releasedate"] = release_date
    plan = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": str(operation_id),
        "environment": environment,
        "action": CREATE_BUILD_ACTION,
        "target": {
            "project": _named_target(project_row, "project"),
            "plan": _named_target(plan_row, "plan"),
        },
        "payload": payload,
    }
    assert_safe_contract(plan)
    return plan


def _create_build_preview_result(plan: dict[str, Any], duplicates: list[dict[str, str]]) -> dict[str, Any]:
    payload = plan["payload"]
    warnings: list[str] = []
    if duplicates:
        warnings.append("A build with this name already exists in the plan; nothing will be created.")
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "operation_id": plan["operation_id"],
        "environment": plan["environment"],
        "mode": "preview",
        "preview_digest": payload_digest(plan),
        "planned_write": not duplicates,
        "target": plan["target"],
        "build": {
            "name": payload["buildname"],
            "notes": payload["buildnotes"],
            "release_date": payload.get("releasedate"),
            "active": bool(payload["active"]),
            "open": bool(payload["open"]),
        },
        "duplicate_found": bool(duplicates),
        "duplicates": duplicates,
        "warnings": warnings,
    }


def _previous_create_build_operation(
    *,
    operation_id: str,
    preview_digest: str,
    environment: str,
    contract_plan: dict[str, Any],
    client: Any,
    audit_dir: str,
) -> dict[str, Any] | None:
    payload = contract_plan["payload"]
    for path, record in find_operation_audits(operation_id, audit_dir, action=CREATE_BUILD_ACTION):
        if record.get("preview_digest") != preview_digest:
            continue
        if record.get("status") == "success":
            return {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": environment,
                "preview_digest": preview_digest,
                "status": "skipped-resume",
                "verification_status": "verified",
                "build": record.get("build"),
                "audit_id": path.name,
            }
        if record.get("status") in {"started", "indeterminate", "verification_failed"}:
            matches = _matching_builds(client.get_builds(payload["testplanid"]), payload["buildname"])
            if len(matches) == 1:
                recovered = {
                    **record,
                    "status": "success",
                    "verification_status": "verified",
                    "recovered": True,
                    "build": matches[0],
                    "finished_at": utc_now_iso(),
                }
                write_operation_audit(recovered, audit_dir, audit_id=path.name)
                return {
                    "schema_version": CONTRACT_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "environment": environment,
                    "preview_digest": preview_digest,
                    "status": "skipped-resume",
                    "verification_status": "verified",
                    "build": matches[0],
                    "audit_id": path.name,
                }
            raise TestLinkError(
                "A previous build creation for this operation is indeterminate and the build cannot be found "
                "unambiguously; check the plan's builds on the TestLink page before retrying.",
                code="BUILD_WRITE_INDETERMINATE",
                raw={"audit_id": path.name, "matches": matches},
            )
    return None


def testlink_create_build(
    *,
    operation_id: str,
    environment: str,
    project: str,
    plan: str,
    name: str,
    notes: str = "",
    release_date: str | None = None,
    active: bool = True,
    open: bool = True,
    write: bool = False,
    preview_digest: str | None = None,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    env_file: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    audit_id: str | None = None
    contract_plan: dict[str, Any] | None = None
    write_attempted = False
    response: Any = None
    try:
        runtime = load_runtime(env_file=env_file, timeout=timeout)
        selected = validate_environment(environment, runtime.environment)
        build_name = _validate_build_inputs(name, release_date, active, open)
        client = write_client(runtime)
        project_row = _resolve_exact_named("project", project, client.get_projects())
        plan_row = _resolve_exact_named(
            "plan",
            plan,
            client.get_project_test_plans(str(project_row.get("id") or "")),
        )
        contract_plan = _build_create_plan(
            operation_id=operation_id,
            environment=selected,
            project_row=project_row,
            plan_row=plan_row,
            name=build_name,
            notes=str(notes or ""),
            release_date=str(release_date) if release_date else None,
            active=active,
            open_=open,
        )
        if not write:
            duplicates = _matching_builds(client.get_builds(contract_plan["payload"]["testplanid"]), build_name)
            return _success(_create_build_preview_result(contract_plan, duplicates))
        validate_preview_digest(contract_plan, str(preview_digest or ""))
        previous = _previous_create_build_operation(
            operation_id=operation_id,
            preview_digest=str(preview_digest),
            environment=selected,
            contract_plan=contract_plan,
            client=client,
            audit_dir=audit_dir,
        )
        if previous is not None:
            return _success(previous)
        payload = contract_plan["payload"]
        duplicates = _matching_builds(client.get_builds(payload["testplanid"]), build_name)
        if duplicates:
            raise TestLinkError(
                "A build with this name already exists in the plan; refusing to create another.",
                code="BUILD_EXISTS",
                raw={"duplicates": duplicates},
            )
        started = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": CREATE_BUILD_ACTION,
                "status": "started",
                "preview_digest": preview_digest,
                "plan": contract_plan,
                "started_at": utc_now_iso(),
            },
            audit_dir,
        )
        audit_id = started.name
        write_attempted = True
        response = client.create_build(payload)
        build_id, rejection = _created_id(response)
        if rejection is not None:
            # An explicit error struct means TestLink did not create the build.
            write_attempted = False
            raise TestLinkError(f"TestLink did not create the build: {rejection}", code="BUILD_REJECTED")
        matches = _matching_builds(client.get_builds(payload["testplanid"]), build_name)
        verified = len(matches) == 1 and (build_id is None or matches[0]["id"] == build_id)
        completed = write_operation_audit(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                "environment": selected,
                "action": CREATE_BUILD_ACTION,
                "status": "success" if verified else "verification_failed",
                "verification_status": "verified" if verified else "verification_failed",
                "preview_digest": preview_digest,
                "plan": contract_plan,
                "response": response,
                "build": matches[0] if verified else {"id": build_id, "name": build_name},
                "readback_matches": matches,
                "finished_at": utc_now_iso(),
            },
            audit_dir,
            audit_id=audit_id,
        )
        if not verified:
            return _failure(
                operation_id,
                "create-build-readback-verification",
                TestLinkError(
                    "TestLink answered the build creation but readback does not show exactly one matching build.",
                    code="BUILD_READBACK_MISMATCH",
                    raw={"audit_id": completed.name, "build_id": build_id, "matches": matches},
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
                "build": matches[0],
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
                        "action": CREATE_BUILD_ACTION,
                        "status": "indeterminate" if write_attempted else "failed",
                        "preview_digest": preview_digest,
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
        return _failure(operation_id, "create-build-write" if write else "create-build-preview", exc)


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name == "testlink_get_latest_results":
        try:
            return testlink_get_latest_results(**(arguments or {}))
        except TypeError as exc:
            operation_id = str((arguments or {}).get("operation_id") or "unknown-operation")
            return _failure(operation_id, "arguments", exc)
    if name == "testlink_create_build":
        try:
            return testlink_create_build(**(arguments or {}))
        except TypeError as exc:
            operation_id = str((arguments or {}).get("operation_id") or "unknown-operation")
            return _failure(operation_id, "arguments", exc)
    if name == "testlink_link_bug":
        try:
            return testlink_link_bug(**(arguments or {}))
        except TypeError as exc:
            operation_id = str((arguments or {}).get("operation_id") or "unknown-operation")
            return _failure(operation_id, "arguments", exc)
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
