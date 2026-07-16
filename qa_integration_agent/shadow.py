from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import CoordinatorError


def _unwrap(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("ok") is True and isinstance(value.get("result"), dict):
        return value["result"]
    return value


def _load(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        raise CoordinatorError(f"Shadow preview file does not exist: {file_path}", code="SHADOW_FILE_NOT_FOUND")
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoordinatorError(f"Shadow preview is not valid JSON: {file_path}", code="SHADOW_INVALID") from exc
    if not isinstance(value, dict):
        raise CoordinatorError("Shadow preview must be a JSON object.", code="SHADOW_INVALID")
    return _unwrap(value)


def compare_previews(legacy: dict[str, Any], modern: dict[str, Any]) -> dict[str, Any]:
    legacy = _unwrap(legacy)
    modern = _unwrap(modern)
    legacy_target = legacy.get("target") if isinstance(legacy.get("target"), dict) else {}
    modern_target = modern.get("target") if isinstance(modern.get("target"), dict) else {}
    target_pairs = {
        "project": (legacy_target.get("project"), modern_target.get("project")),
        "plan": (legacy_target.get("testplan") or legacy_target.get("plan"), modern_target.get("plan")),
        "platform": (legacy_target.get("platform"), modern_target.get("platform")),
        "build": (legacy_target.get("build"), modern_target.get("build")),
    }
    mismatches: list[dict[str, Any]] = []
    for field, (left, right) in target_pairs.items():
        if str(left or "") != str(right or ""):
            mismatches.append({"field": f"target.{field}", "legacy": left, "modern": right})
    for field in ("write_count", "ignored_count"):
        if int(legacy.get(field) or 0) != int(modern.get(field) or 0):
            mismatches.append(
                {"field": field, "legacy": int(legacy.get(field) or 0), "modern": int(modern.get(field) or 0)}
            )
    legacy_failures = {
        str(item.get("external_id") or item.get("testcase_external_id") or "")
        for item in legacy.get("failures_to_write") or []
        if isinstance(item, dict)
    }
    modern_failures = {
        str(item.get("testcase_external_id") or "")
        for item in modern.get("items") or []
        if isinstance(item, dict) and item.get("status") == "f"
    }
    if legacy_failures != modern_failures:
        mismatches.append(
            {
                "field": "failure_testcases",
                "legacy": sorted(legacy_failures),
                "modern": sorted(modern_failures),
            }
        )
    legacy_redmine = {
        str(item.get("external_id") or ""): str(item.get("action") or "")
        for item in ((legacy.get("redmine") or {}).get("issues_to_create_or_reuse") or [])
        if isinstance(item, dict)
    }
    modern_redmine = {
        str(item.get("testcase_external_id") or ""): str(item.get("redmine_action") or "")
        for item in modern.get("items") or []
        if isinstance(item, dict) and item.get("redmine_action") != "none"
    }
    for external_id in sorted(set(legacy_redmine) | set(modern_redmine)):
        legacy_action = legacy_redmine.get(external_id, "")
        modern_action = modern_redmine.get(external_id, "")
        compatible_action = legacy_action == modern_action or (
            legacy_action == "create-or-reuse" and modern_action in {"create", "reuse"}
        )
        if not compatible_action:
            mismatches.append(
                {
                    "field": f"redmine.{external_id}",
                    "legacy": legacy_action,
                    "modern": modern_action,
                }
            )
    return {
        "compatible": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def compare_preview_files(legacy_preview_file: str, modern_preview_file: str) -> dict[str, Any]:
    return compare_previews(_load(legacy_preview_file), _load(modern_preview_file))
