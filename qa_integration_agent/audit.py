from __future__ import annotations

import datetime as _datetime
import json
import uuid
from pathlib import Path
from typing import Any

from qa_mcp_contracts import ensure_directory, write_text_atomically
from testlink_agent_core.errors import redact_secrets

from .errors import CoordinatorError


DEFAULT_AUDIT_DIR = "local/qa_audit"
# 1.0: single-report workflow audit; 2.0: multi-report audit (contracts/v2).
SUPPORTED_AUDIT_SCHEMA_VERSIONS = ("1.0", "2.0")


def utc_now_iso() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).replace(microsecond=0).isoformat()


def write_workflow_audit(
    record: dict[str, Any],
    audit_dir: str | Path | None = None,
    *,
    audit_id: str | None = None,
) -> Path:
    directory = ensure_directory(audit_dir or DEFAULT_AUDIT_DIR, label="Workflow audit")
    safe = redact_secrets(record)
    if audit_id:
        path = directory / Path(audit_id).name
    else:
        operation_id = str(safe.get("operation_id") or "operation")
        path = directory / f"{operation_id}-qa-workflow-{uuid.uuid4().hex}.json"
    write_text_atomically(
        path, json.dumps(safe, indent=2, ensure_ascii=False, default=str) + "\n", label="Workflow audit"
    )
    return path


def read_workflow_audit(path: str | Path) -> dict[str, Any]:
    audit_path = Path(path)
    if not audit_path.exists():
        raise CoordinatorError(f"Workflow audit does not exist: {audit_path}", code="AUDIT_NOT_FOUND")
    try:
        record = json.loads(audit_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoordinatorError(f"Workflow audit is not valid JSON: {audit_path}", code="AUDIT_INVALID") from exc
    if not isinstance(record, dict) or record.get("schema_version") not in SUPPORTED_AUDIT_SCHEMA_VERSIONS:
        raise CoordinatorError("Unsupported workflow audit schema.", code="AUDIT_INVALID")
    return redact_secrets(record)
