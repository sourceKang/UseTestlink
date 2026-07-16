from __future__ import annotations

import datetime as _datetime
import json
import uuid
from pathlib import Path
from typing import Any

from qa_mcp_contracts import atomic_replace
from testlink_agent_core.errors import redact_secrets

from .errors import CoordinatorError


DEFAULT_AUDIT_DIR = "local/qa_audit"


def utc_now_iso() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).replace(microsecond=0).isoformat()


def write_workflow_audit(
    record: dict[str, Any],
    audit_dir: str | Path | None = None,
    *,
    audit_id: str | None = None,
) -> Path:
    directory = Path(audit_dir or DEFAULT_AUDIT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    safe = redact_secrets(record)
    if audit_id:
        path = directory / Path(audit_id).name
    else:
        operation_id = str(safe.get("operation_id") or "operation")
        path = directory / f"{operation_id}-qa-workflow-{uuid.uuid4().hex}.json"
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(safe, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    atomic_replace(temp_path, path)
    return path


def read_workflow_audit(path: str | Path) -> dict[str, Any]:
    audit_path = Path(path)
    if not audit_path.exists():
        raise CoordinatorError(f"Workflow audit does not exist: {audit_path}", code="AUDIT_NOT_FOUND")
    try:
        record = json.loads(audit_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoordinatorError(f"Workflow audit is not valid JSON: {audit_path}", code="AUDIT_INVALID") from exc
    if not isinstance(record, dict) or record.get("schema_version") != "1.0":
        raise CoordinatorError("Unsupported workflow audit schema.", code="AUDIT_INVALID")
    return redact_secrets(record)
