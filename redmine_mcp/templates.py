from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qa_mcp_contracts import assert_safe_contract

from .errors import RedmineMcpError
from .policy import blocked_manager_fields


def load_template(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    template_path = Path(path)
    if not template_path.exists():
        raise RedmineMcpError(f"Redmine template does not exist: {template_path}", code="TEMPLATE_NOT_FOUND")
    try:
        template = json.loads(template_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RedmineMcpError(
            f"Redmine template is not valid JSON: {template_path}: {exc}",
            code="TEMPLATE_INVALID",
        ) from exc
    if not isinstance(template, dict):
        raise RedmineMcpError("Redmine template must be a JSON object.", code="TEMPLATE_INVALID")
    assert_safe_contract(template)
    return template


def _field_key(field: dict[str, Any]) -> str:
    return str(field.get("name") or field.get("id") or "").strip().casefold()


def validate_template(template: dict[str, Any]) -> dict[str, Any]:
    required_top_level = ["project_id", "tracker_id", "priority_id"]
    missing = [name for name in required_top_level if template.get(name) in (None, "")]
    if missing:
        raise RedmineMcpError(
            "Redmine template is missing required fields: " + ", ".join(missing),
            code="TEMPLATE_INVALID",
        )
    if template.get("status_id") not in (None, ""):
        raise RedmineMcpError(
            "Redmine template must not set status_id; workflow status remains human-owned.",
            code="RESTRICTED_FIELD",
        )
    raw_fields = template.get("custom_fields") or []
    if not isinstance(raw_fields, list):
        raise RedmineMcpError("Template custom_fields must be an array.", code="TEMPLATE_INVALID")
    keys: set[str] = set()
    field_ids: set[str] = set()
    field_names: set[str] = set()
    fields: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for index, field in enumerate(raw_fields):
        if not isinstance(field, dict) or field.get("id") in (None, "") or "value" not in field:
            raise RedmineMcpError(
                f"Template custom_fields[{index}] requires id and value.",
                code="TEMPLATE_INVALID",
            )
        key = _field_key(field)
        if not key:
            raise RedmineMcpError(
                f"Template custom_fields[{index}] requires id or name.",
                code="TEMPLATE_INVALID",
            )
        if key in keys:
            raise RedmineMcpError(f"Duplicate Redmine custom field: {key}", code="TEMPLATE_INVALID")
        keys.add(key)
        field_ids.add(str(field.get("id")))
        if field.get("name") not in (None, ""):
            field_names.add(str(field.get("name")).strip().casefold())
        value = field.get("value")
        if isinstance(value, str) and "{{" in value:
            unresolved.append(str(field.get("name") or field.get("id")))
        fields.append(
            {
                "id": field.get("id"),
                "name": str(field.get("name") or ""),
                "has_value": value not in (None, ""),
            }
        )
    required_custom_fields = template.get("required_custom_fields") or []
    if not isinstance(required_custom_fields, list):
        raise RedmineMcpError("required_custom_fields must be an array.", code="TEMPLATE_INVALID")
    missing_required: list[str] = []
    normalized_required: list[str] = []
    for raw_required in required_custom_fields:
        if isinstance(raw_required, dict):
            required_id = raw_required.get("id")
            required_name = str(raw_required.get("name") or required_id or "").strip()
        else:
            required_id = raw_required if str(raw_required).isdigit() else None
            required_name = str(raw_required).strip()
        normalized_required.append(required_name or str(required_id))
        matches = (
            required_id not in (None, "") and str(required_id) in field_ids
        ) or (
            bool(required_name) and required_name.casefold() in field_names
        )
        if not matches:
            missing_required.append(required_name or str(required_id))
    if missing_required:
        raise RedmineMcpError(
            "Required Redmine custom fields are not defined: " + ", ".join(missing_required),
            code="TEMPLATE_INVALID",
        )
    blocked = blocked_manager_fields(template)
    return {
        "project_id": str(template["project_id"]),
        "tracker_id": str(template["tracker_id"]),
        "priority_id": str(template["priority_id"]),
        "custom_fields": fields,
        "required_custom_fields": normalized_required,
        "unresolved_fields": unresolved,
        "blocked_fields": blocked,
    }


def merge_template_values(
    template: dict[str, Any],
    *,
    project_id: str | None,
    tracker_id: str | None,
    priority_id: str | None,
    custom_fields: Any,
    category_id: str | None,
    assigned_to_id: str | None,
    fixed_version_id: str | None,
) -> dict[str, Any]:
    summary = validate_template(template) if template else {
        "unresolved_fields": [],
        "required_custom_fields": [],
    }
    selected_custom_fields = custom_fields if custom_fields is not None else template.get("custom_fields")
    if selected_custom_fields is not None:
        for field in selected_custom_fields:
            value = field.get("value") if isinstance(field, dict) else None
            if isinstance(value, str) and "{{" in value:
                raise RedmineMcpError(
                    "Redmine template contains unresolved custom field tokens; the Coordinator must render them first.",
                    code="TEMPLATE_UNRESOLVED",
                )
    return {
        "project_id": project_id or template.get("project_id"),
        "tracker_id": tracker_id or template.get("tracker_id"),
        "priority_id": priority_id or template.get("priority_id"),
        "custom_fields": selected_custom_fields,
        "category_id": category_id if category_id is not None else template.get("category_id"),
        "assigned_to_id": assigned_to_id if assigned_to_id is not None else template.get("assigned_to_id"),
        "fixed_version_id": (
            fixed_version_id if fixed_version_id is not None else template.get("fixed_version_id")
        ),
        "template_summary": summary,
    }
