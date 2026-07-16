from __future__ import annotations

import datetime as _datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from qa_mcp_contracts import atomic_replace

from .config import DEFAULT_AUDIT_DIR
from .errors import TestLinkError, redact_secrets


AUDIT_SCHEMA_VERSION = "1.0"


def utc_now_iso() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).replace(microsecond=0).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_audit_record(
    *,
    operation: str,
    mode: str,
    report_path: Path | None = None,
    profile: dict[str, Any] | None = None,
    testlink_target: dict[str, Any] | None = None,
    redmine_target: dict[str, Any] | None = None,
    report_schema: str | None = None,
    parsed_count: int | None = None,
    write_count: int | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "operation": operation,
        "mode": mode,
        "started_at": started_at or utc_now_iso(),
        "profile": profile or {},
        "testlink_target": testlink_target or {},
        "redmine_target": redmine_target or {},
        "report_schema": report_schema,
        "parsed_count": parsed_count,
        "write_count": write_count,
        "results": [],
        "errors": [],
    }
    if report_path is not None:
        record["report_path"] = str(report_path)
        record["report_sha256"] = file_sha256(report_path)
    return record


def append_audit_result(record: dict[str, Any], item: dict[str, Any]) -> None:
    record.setdefault("results", []).append(item)


def append_audit_error(record: dict[str, Any], item: dict[str, Any]) -> None:
    record.setdefault("errors", []).append(item)


def read_audit_record(path: Path | str) -> dict[str, Any]:
    audit_path = Path(path)
    if not audit_path.exists():
        raise TestLinkError(f"Resume audit file does not exist: {audit_path}")
    try:
        record = json.loads(audit_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TestLinkError(f"Resume audit file is not valid JSON: {audit_path}: {exc}") from exc
    if not isinstance(record, dict):
        raise TestLinkError(f"Resume audit file must contain a JSON object: {audit_path}")
    if record.get("schema_version") != AUDIT_SCHEMA_VERSION:
        raise TestLinkError(
            f"Unsupported resume audit schema: {record.get('schema_version')}. "
            f"Expected {AUDIT_SCHEMA_VERSION}."
        )
    return record


def validate_resume_audit(
    resume_record: dict[str, Any],
    current_record: dict[str, Any],
) -> None:
    checks = (
        "operation",
        "report_sha256",
        "report_schema",
        "profile",
        "testlink_target",
    )
    mismatches = [key for key in checks if resume_record.get(key) != current_record.get(key)]
    if mismatches:
        raise TestLinkError("Resume audit does not match this upload target: " + ", ".join(mismatches))


def resume_items_by_external_id(resume_record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in resume_record.get("results") or []:
        if not isinstance(item, dict):
            continue
        external_id = str(item.get("external_id") or "").strip()
        if not external_id:
            continue
        indexed[external_id] = item
    return indexed


def resume_item_has_testlink_success(item: dict[str, Any] | None) -> bool:
    return bool(item) and item.get("testlink_write") == "success"


def resume_item_needs_redmine_comment(item: dict[str, Any] | None) -> bool:
    return resume_item_has_testlink_success(item) and item.get("redmine_comment") == "failed"


def finalize_audit_record(record: dict[str, Any], *, status: str | None = None) -> dict[str, Any]:
    record["finished_at"] = utc_now_iso()
    if status is None:
        status = "failed" if record.get("errors") else "success"
    record["status"] = status
    return record


def audit_filename(record: dict[str, Any]) -> str:
    started = str(record.get("started_at") or utc_now_iso())
    safe_started = (
        started.replace(":", "")
        .replace("-", "")
        .replace("+", "Z")
        .replace(".", "")
    )
    operation = str(record.get("operation") or "operation").replace("_", "-")
    return f"{safe_started}-{operation}.json"


def write_audit_record(record: dict[str, Any], audit_dir: Path | str | None = DEFAULT_AUDIT_DIR) -> Path:
    directory = Path(audit_dir or DEFAULT_AUDIT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    redacted = redact_secrets(record)
    record.clear()
    record.update(redacted)
    path = directory / audit_filename(record)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    atomic_replace(temp_path, path)
    return path
