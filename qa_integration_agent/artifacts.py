from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from qa_mcp_contracts import CONTRACT_SCHEMA_VERSION, atomic_replace, payload_digest
from testlink_agent_core.errors import redact_secrets

from .errors import CoordinatorError


DEFAULT_PREVIEW_DIR = "local/qa_previews"


def write_preview_artifact(
    plan: dict[str, Any],
    review: dict[str, Any],
    artifact_dir: str | Path = DEFAULT_PREVIEW_DIR,
) -> Path:
    directory = Path(artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    operation_id = str(plan.get("operation_id") or "operation")
    path = directory / f"{operation_id}-qa-preview-{uuid.uuid4().hex}.json"
    payload = redact_secrets(
        {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "artifact_type": "qa-preview-plan",
            "operation_id": operation_id,
            "preview_digest": plan.get("preview_digest"),
            "plan": plan,
            "review": review,
        }
    )
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    atomic_replace(temp_path, path)
    return path


def read_preview_artifact(
    artifact_file: str | Path,
    *,
    operation_id: str,
    preview_digest: str,
) -> dict[str, Any]:
    path = Path(artifact_file)
    if not path.exists():
        raise CoordinatorError(f"Preview artifact does not exist: {path}", code="PREVIEW_ARTIFACT_NOT_FOUND")
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoordinatorError("Preview artifact is not valid JSON.", code="PREVIEW_ARTIFACT_INVALID") from exc
    if not isinstance(artifact, dict) or artifact.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise CoordinatorError("Unsupported preview artifact schema.", code="PREVIEW_ARTIFACT_INVALID")
    if artifact.get("artifact_type") != "qa-preview-plan":
        raise CoordinatorError("Unexpected preview artifact type.", code="PREVIEW_ARTIFACT_INVALID")
    plan = artifact.get("plan")
    if not isinstance(plan, dict):
        raise CoordinatorError("Preview artifact plan is missing.", code="PREVIEW_ARTIFACT_INVALID")
    if str(artifact.get("operation_id") or "") != operation_id or str(plan.get("operation_id") or "") != operation_id:
        raise CoordinatorError("Preview artifact operation_id does not match.", code="PREVIEW_MISMATCH")
    stored_digest = str(artifact.get("preview_digest") or "")
    if stored_digest != preview_digest or str(plan.get("preview_digest") or "") != preview_digest:
        raise CoordinatorError("Preview artifact digest does not match confirmation.", code="PREVIEW_MISMATCH")
    digest_payload = dict(plan)
    digest_payload.pop("preview_digest", None)
    if payload_digest(digest_payload) != preview_digest:
        raise CoordinatorError("Preview artifact content was modified.", code="PREVIEW_MISMATCH")
    return redact_secrets(plan)
