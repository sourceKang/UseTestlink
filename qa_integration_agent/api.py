from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from qa_mcp_contracts import CONTRACT_SCHEMA_VERSION
from testlink_agent_core.errors import redact_secrets

from .artifacts import DEFAULT_PREVIEW_DIR, read_preview_artifact, write_preview_artifact
from .audit import DEFAULT_AUDIT_DIR, read_workflow_audit
from .coordinator import QaCoordinator
from .coordinator import file_sha256
from .errors import CoordinatorError, normalize_error
from .shadow import compare_preview_files


def _success(result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "code": 0, "result": redact_secrets(result)}


def _failure(operation_id: str, stage: str, error: BaseException) -> dict[str, Any]:
    return {
        "ok": False,
        "code": 1,
        "error": {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": str(operation_id or "unknown-operation"),
            "error": normalize_error(error, stage),
        },
    }


def _audit_summary(record: dict[str, Any], audit_file: str) -> dict[str, Any]:
    items = [item for item in record.get("items") or [] if isinstance(item, dict)]
    states = sorted({str(item.get("state") or "unknown") for item in items})
    return {
        "schema_version": record.get("schema_version"),
        "operation_id": record.get("operation_id"),
        "environment": record.get("environment"),
        "status": record.get("status"),
        "preview_digest": record.get("preview_digest"),
        "audit_file": audit_file,
        "item_count": len(items),
        "error_count": len(record.get("errors") or []),
        "state_counts": {
            state: sum(1 for item in items if str(item.get("state") or "unknown") == state)
            for state in states
        },
    }


def _plan_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "operation_id",
        "correlation_id",
        "environment",
        "project",
        "plan",
        "platform",
        "build",
        "report",
        "skip_policy",
        "redmine_create_bugs",
        "redmine_project_id",
        "redmine_tracker_id",
        "redmine_priority_id",
        "redmine_severity",
        "redmine_custom_priority",
        "redmine_template_file",
        "redmine_custom_fields",
    }
    return {key: value for key, value in kwargs.items() if key in allowed}


def qa_preview_report_artifact(
    *,
    artifact_dir: str = DEFAULT_PREVIEW_DIR,
    coordinator: QaCoordinator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    operation_id = str(kwargs.get("operation_id") or "unknown-operation")
    try:
        selected = coordinator or QaCoordinator()
        plan = selected.build_plan(**_plan_kwargs(kwargs))
        review = selected.public_preview(plan, include_items=True)
        artifact = write_preview_artifact(plan, review, artifact_dir)
        compact = selected.public_preview(plan, include_items=False)
        compact["preview_artifact"] = str(artifact)
        compact["review_path"] = "review"
        return _success(compact)
    except Exception as exc:
        return _failure(operation_id, "qa-preview", exc)


def qa_preview_report_import(*, coordinator: QaCoordinator | None = None, **kwargs: Any) -> dict[str, Any]:
    """Compatibility v1 preview that returns the full inline item list."""
    operation_id = str(kwargs.get("operation_id") or "unknown-operation")
    try:
        selected = coordinator or QaCoordinator()
        plan = selected.build_plan(**_plan_kwargs(kwargs))
        return _success(selected.public_preview(plan, include_items=True))
    except Exception as exc:
        return _failure(operation_id, "qa-preview", exc)


def qa_execute_preview_artifact(
    *,
    operation_id: str,
    preview_artifact: str,
    preview_digest: str,
    write: bool = False,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    coordinator: QaCoordinator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    operation_id = str(operation_id or "unknown-operation")
    try:
        if write is not True:
            raise CoordinatorError(
                "qa_execute_preview_artifact requires write=true after explicit preview confirmation.",
                code="CONFIRMATION_REQUIRED",
            )
        selected = coordinator or QaCoordinator()
        plan = read_preview_artifact(
            preview_artifact,
            operation_id=operation_id,
            preview_digest=preview_digest,
        )
        report = str(plan.get("report") or "")
        if not report or file_sha256(Path(report)) != plan.get("input_digest"):
            raise CoordinatorError("Report content changed after preview.", code="PREVIEW_MISMATCH")
        result = selected.execute_plan(
            plan,
            confirmed_preview_digest=preview_digest,
            report=report,
            audit_dir=audit_dir,
        )
        audit = result["audit"]
        return _success(
            {
                "status": result["status"],
                "operation_id": operation_id,
                "preview_digest": preview_digest,
                "audit_id": result["audit_id"],
                "audit_file": str(Path(audit_dir) / result["audit_id"]),
                "item_count": len(audit.get("items") or []),
                "error_count": len(audit.get("errors") or []),
            }
        )
    except Exception as exc:
        return _failure(operation_id, "qa-execute", exc)


def qa_resume_preview_artifact(
    *,
    operation_id: str,
    preview_artifact: str,
    audit_file: str,
    write: bool = False,
    coordinator: QaCoordinator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    operation_id = str(operation_id or "unknown-operation")
    try:
        if write is not True:
            raise CoordinatorError(
                "qa_resume_preview_artifact requires write=true after explicit resume request.",
                code="CONFIRMATION_REQUIRED",
            )
        previous = read_workflow_audit(audit_file)
        if previous.get("operation_id") != operation_id:
            raise CoordinatorError("Resume operation_id does not match audit.", code="RESUME_MISMATCH")
        selected = coordinator or QaCoordinator()
        preview_digest = str(previous.get("preview_digest") or "")
        plan = read_preview_artifact(
            preview_artifact,
            operation_id=operation_id,
            preview_digest=preview_digest,
        )
        report = str(plan.get("report") or "")
        if not report or file_sha256(Path(report)) != plan.get("input_digest"):
            raise CoordinatorError("Report content changed after preview.", code="RESUME_MISMATCH")
        result = selected.execute_plan(
            plan,
            confirmed_preview_digest=preview_digest,
            report=report,
            audit_dir=str(Path(audit_file).parent),
            resume_audit=audit_file,
        )
        audit = result["audit"]
        return _success(
            {
                "status": result["status"],
                "operation_id": operation_id,
                "preview_digest": preview_digest,
                "audit_id": result["audit_id"],
                "audit_file": str(Path(audit_file)),
                "item_count": len(audit.get("items") or []),
                "error_count": len(audit.get("errors") or []),
            }
        )
    except Exception as exc:
        return _failure(operation_id, "qa-resume", exc)


def qa_execute_report_import(
    *,
    preview_digest: str,
    write: bool = False,
    audit_dir: str = DEFAULT_AUDIT_DIR,
    coordinator: QaCoordinator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility path that rebuilds the reviewed v1 plan from unchanged arguments."""
    operation_id = str(kwargs.get("operation_id") or "unknown-operation")
    try:
        if write is not True:
            raise CoordinatorError(
                "qa_execute_report_import requires write=true after explicit preview confirmation.",
                code="CONFIRMATION_REQUIRED",
            )
        selected = coordinator or QaCoordinator()
        plan = selected.build_plan(**_plan_kwargs(kwargs))
        return _success(
            selected.execute_plan(
                plan,
                confirmed_preview_digest=preview_digest,
                report=str(kwargs.get("report") or ""),
                audit_dir=audit_dir,
            )
        )
    except Exception as exc:
        return _failure(operation_id, "qa-execute", exc)


def qa_resume_report_import(
    *,
    audit_file: str,
    write: bool = False,
    coordinator: QaCoordinator | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility path that rebuilds the v1 plan before audited resume."""
    operation_id = str(kwargs.get("operation_id") or "unknown-operation")
    try:
        if write is not True:
            raise CoordinatorError(
                "qa_resume_report_import requires write=true after explicit resume request.",
                code="CONFIRMATION_REQUIRED",
            )
        previous = read_workflow_audit(audit_file)
        if previous.get("operation_id") != operation_id:
            raise CoordinatorError("Resume operation_id does not match audit.", code="RESUME_MISMATCH")
        selected = coordinator or QaCoordinator()
        plan = selected.build_plan(**_plan_kwargs(kwargs))
        return _success(
            selected.execute_plan(
                plan,
                confirmed_preview_digest=str(previous.get("preview_digest") or ""),
                report=str(kwargs.get("report") or ""),
                audit_dir=str(Path(audit_file).parent),
                resume_audit=audit_file,
            )
        )
    except Exception as exc:
        return _failure(operation_id, "qa-resume", exc)


def qa_get_operation(*, operation_id: str, audit_file: str, include_details: bool = False) -> dict[str, Any]:
    try:
        record = read_workflow_audit(audit_file)
        if record.get("operation_id") != operation_id:
            raise CoordinatorError("operation_id does not match audit.", code="AUDIT_MISMATCH")
        return _success(record if include_details else _audit_summary(record, audit_file))
    except Exception as exc:
        return _failure(operation_id, "qa-get-operation", exc)


def qa_validate_traceability(*, operation_id: str, audit_file: str) -> dict[str, Any]:
    try:
        record = read_workflow_audit(audit_file)
        if record.get("operation_id") != operation_id:
            raise CoordinatorError("operation_id does not match audit.", code="AUDIT_MISMATCH")
        result = QaCoordinator.validate_traceability(record)
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                **result,
            }
        )
    except Exception as exc:
        return _failure(operation_id, "qa-validate-traceability", exc)


def qa_compare_shadow_previews(
    *,
    operation_id: str,
    legacy_preview_file: str,
    modern_preview_file: str,
) -> dict[str, Any]:
    try:
        return _success(
            {
                "schema_version": CONTRACT_SCHEMA_VERSION,
                "operation_id": operation_id,
                **compare_preview_files(legacy_preview_file, modern_preview_file),
            }
        )
    except Exception as exc:
        return _failure(operation_id, "qa-shadow-compare", exc)


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "qa_preview_report_artifact": qa_preview_report_artifact,
    "qa_preview_report_import": qa_preview_report_import,
    "qa_execute_preview_artifact": qa_execute_preview_artifact,
    "qa_resume_preview_artifact": qa_resume_preview_artifact,
    "qa_execute_report_import": qa_execute_report_import,
    "qa_resume_report_import": qa_resume_report_import,
    "qa_get_operation": qa_get_operation,
    "qa_validate_traceability": qa_validate_traceability,
    "qa_compare_shadow_previews": qa_compare_shadow_previews,
}


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name not in TOOLS:
        return {"ok": False, "code": 1, "error": {"type": "UnknownTool", "message": f"Unknown tool: {name}"}}
    try:
        return TOOLS[name](**(arguments or {}))
    except TypeError as exc:
        operation_id = str((arguments or {}).get("operation_id") or "unknown-operation")
        return _failure(operation_id, "arguments", exc)
