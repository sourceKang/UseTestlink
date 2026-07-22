from __future__ import annotations

import datetime as _datetime
import json
import uuid
from pathlib import Path
from typing import Any

from qa_mcp_contracts import atomic_replace
from testlink_agent_core.errors import redact_secrets

from .config import DEFAULT_AUDIT_DIR


def utc_now_iso() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).replace(microsecond=0).isoformat()


def write_operation_audit(
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
        action = str(safe.get("action") or "testlink")
        path = directory / f"{operation_id}-{action}-{uuid.uuid4().hex}.json"
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(safe, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    atomic_replace(temp_path, path)
    return path


def find_operation_audits(
    operation_id: str,
    audit_dir: str | Path | None = None,
    *,
    action: str | None = None,
) -> list[tuple[Path, dict[str, Any]]]:
    directory = Path(audit_dir or DEFAULT_AUDIT_DIR)
    if not directory.exists():
        return []
    matches: list[tuple[Path, dict[str, Any]]] = []
    action_pattern = action or "*"
    for path in directory.glob(f"{operation_id}-{action_pattern}-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(record, dict)
            and record.get("operation_id") == operation_id
            and (action is None or record.get("action") == action)
        ):
            matches.append((path, redact_secrets(record)))
    matches.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
    return matches
