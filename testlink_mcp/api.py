from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from qa_mcp_contracts import CONTRACT_SCHEMA_VERSION, assert_safe_contract, payload_digest, validate_preview_digest
from testlink_agent_core.api import call_tool as legacy_call_tool
from testlink_agent_core.api import report_result as legacy_report_result
from testlink_agent_core.errors import TestLinkError, normalize_testlink_error, redact_secrets

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


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name == "testlink_report_execution":
        try:
            return testlink_report_execution(**(arguments or {}))
        except TypeError as exc:
            operation_id = str((arguments or {}).get("operation_id") or "unknown-operation")
            return _failure(operation_id, "arguments", exc)
    if name not in ALLOWED_TOOL_NAMES or name in EXCLUDED_LEGACY_TOOLS:
        return {"ok": False, "code": 1, "error": {"type": "UnknownTool", "message": f"Unknown tool: {name}"}}
    return legacy_call_tool(name, arguments)
