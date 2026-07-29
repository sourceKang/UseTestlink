from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qa_mcp_contracts import assert_safe_contract

from .errors import RedmineMcpError
from .policy import blocked_manager_fields
from .text_format import normalize_text_format_contract


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


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value or all(_blank(item) for item in value)
    return False


def _validate_severity_contract(template: dict[str, Any]) -> dict[str, Any] | None:
    raw = template.get("severity")
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict):
        raise RedmineMcpError("Template severity must be an object.", code="TEMPLATE_INVALID")
    if raw.get("transport_field") != "priority_id":
        raise RedmineMcpError(
            "Template severity.transport_field must be priority_id.",
            code="TEMPLATE_INVALID",
        )
    raw_values = raw.get("values")
    if not isinstance(raw_values, dict) or not raw_values:
        raise RedmineMcpError("Template severity.values must be a non-empty object.", code="TEMPLATE_INVALID")
    values: dict[str, str] = {}
    labels: dict[str, str] = {}
    ids: set[str] = set()
    for raw_label, raw_id in raw_values.items():
        label = str(raw_label or "").strip()
        priority_id = str(raw_id or "").strip()
        normalized_label = label.casefold()
        if not label or not priority_id:
            raise RedmineMcpError(
                "Template severity values require non-empty labels and priority IDs.",
                code="TEMPLATE_INVALID",
            )
        if normalized_label in labels or priority_id in ids:
            raise RedmineMcpError(
                "Template severity labels and priority IDs must be unique.",
                code="TEMPLATE_INVALID",
            )
        labels[normalized_label] = label
        values[label] = priority_id
        ids.add(priority_id)
    default = str(raw.get("default") or "").strip()
    if default:
        canonical = labels.get(default.casefold())
        if canonical is None:
            raise RedmineMcpError(
                f"Template severity.default is not in severity.values: {default}",
                code="TEMPLATE_INVALID",
            )
        default = canonical
    return {
        "transport_field": "priority_id",
        "default": default or None,
        "values": values,
    }


def _validate_priority_contract(template: dict[str, Any]) -> dict[str, Any] | None:
    raw = template.get("priority")
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict):
        raise RedmineMcpError("Template priority must be an object.", code="TEMPLATE_INVALID")
    if raw.get("transport_field") != "custom_fields":
        raise RedmineMcpError(
            "Template priority.transport_field must be custom_fields.",
            code="TEMPLATE_INVALID",
        )
    field_id = str(raw.get("custom_field_id") or "").strip()
    if not field_id:
        raise RedmineMcpError(
            "Template priority.custom_field_id is required.",
            code="TEMPLATE_INVALID",
        )
    raw_allowed = raw.get("allowed_values")
    if raw_allowed is None:
        allowed_values: list[str] = []
    elif isinstance(raw_allowed, list):
        allowed_values = [str(value).strip() for value in raw_allowed if str(value).strip()]
        if len({value.casefold() for value in allowed_values}) != len(allowed_values):
            raise RedmineMcpError(
                "Template priority.allowed_values must be unique.",
                code="TEMPLATE_INVALID",
            )
    else:
        raise RedmineMcpError(
            "Template priority.allowed_values must be an array when provided.",
            code="TEMPLATE_INVALID",
        )
    default = raw.get("default")
    if not _blank(default):
        canonical = next(
            (value for value in allowed_values if value.casefold() == str(default).strip().casefold()),
            None,
        )
        if canonical is None:
            raise RedmineMcpError(
                "Template priority.default is not in priority.allowed_values.",
                code="TEMPLATE_INVALID",
            )
        default = canonical
    else:
        default = None
    return {
        "transport_field": "custom_fields",
        "custom_field_id": field_id,
        "allowed_values": allowed_values,
        "default": default,
    }


def _resolve_severity(
    template: dict[str, Any],
    *,
    severity: str | None,
    priority_id: str | None,
) -> tuple[str, dict[str, Any]]:
    contract = _validate_severity_contract(template)
    raw_priority_id = priority_id if priority_id not in (None, "") else template.get("priority_id")
    if contract is None:
        if severity not in (None, ""):
            raise RedmineMcpError(
                "severity requires a validated template severity mapping.",
                code="SEVERITY_MAPPING_REQUIRED",
            )
        resolved_id = str(raw_priority_id or "").strip()
        if not resolved_id:
            raise RedmineMcpError("priority_id is required.", code="TEMPLATE_INVALID")
        return resolved_id, {
            "label": None,
            "transport_field": "priority_id",
            "priority_id": resolved_id,
            "display": f"Redmine priority_id={resolved_id}",
        }

    values = contract["values"]
    by_label = {label.casefold(): (label, value) for label, value in values.items()}
    by_id = {value: label for label, value in values.items()}
    requested_label = str(severity or contract.get("default") or "").strip()
    if requested_label:
        selected = by_label.get(requested_label.casefold())
        if selected is None:
            raise RedmineMcpError(
                f"Unknown Severity label: {requested_label}",
                code="INVALID_SEVERITY",
            )
        canonical_label, resolved_id = selected
        if raw_priority_id not in (None, "") and str(raw_priority_id).strip() != resolved_id:
            raise RedmineMcpError(
                "Severity label conflicts with priority_id; use the verified template mapping.",
                code="SEVERITY_PRIORITY_CONFLICT",
            )
    else:
        resolved_id = str(raw_priority_id or "").strip()
        canonical_label = by_id.get(resolved_id, "")
        if not canonical_label:
            raise RedmineMcpError(
                "A Severity label or a priority_id present in the verified mapping is required.",
                code="INVALID_SEVERITY",
            )
    return resolved_id, {
        "label": canonical_label,
        "transport_field": "priority_id",
        "priority_id": resolved_id,
        "display": f"{canonical_label} (Redmine priority_id={resolved_id})",
    }


def _resolve_priority(
    template: dict[str, Any],
    *,
    custom_priority: Any,
    custom_fields: Any,
) -> tuple[Any, dict[str, Any] | None]:
    contract = _validate_priority_contract(template)
    if contract is None:
        if custom_priority not in (None, ""):
            raise RedmineMcpError(
                "custom_priority requires a validated template priority contract.",
                code="PRIORITY_CONTRACT_REQUIRED",
            )
        return custom_fields, None

    if custom_fields in (None, ""):
        selected_fields: list[dict[str, Any]] = []
    elif isinstance(custom_fields, list):
        selected_fields = [dict(field) if isinstance(field, dict) else field for field in custom_fields]
    else:
        raise RedmineMcpError("custom_fields must be an array.", code="INVALID_ARGUMENT")
    field_id = contract["custom_field_id"]
    matching = [
        field
        for field in selected_fields
        if isinstance(field, dict) and str(field.get("id") or "").strip() == field_id
    ]
    if len(matching) > 1:
        raise RedmineMcpError(
            f"Duplicate custom Priority field ID: {field_id}",
            code="TEMPLATE_INVALID",
        )
    direct_value = matching[0].get("value") if matching else None
    if custom_priority is not None and matching and custom_priority != direct_value:
        raise RedmineMcpError(
            "custom_priority conflicts with custom_fields for the configured Priority field.",
            code="CUSTOM_PRIORITY_CONFLICT",
        )
    requested = custom_priority if custom_priority is not None else direct_value
    if requested is None:
        requested = contract.get("default")
    selected_fields = [
        field
        for field in selected_fields
        if not (isinstance(field, dict) and str(field.get("id") or "").strip() == field_id)
    ]
    if not _blank(requested):
        allowed_values = contract["allowed_values"]
        canonical = next(
            (value for value in allowed_values if value.casefold() == str(requested).strip().casefold()),
            None,
        )
        if canonical is None:
            message = (
                "Custom Priority values are not confirmed; leave Priority blank."
                if not allowed_values
                else f"Unknown custom Priority value: {requested}"
            )
            raise RedmineMcpError(message, code="INVALID_CUSTOM_PRIORITY")
        requested = canonical
        selected_fields.append({"id": field_id, "value": requested})
    else:
        requested = None
    return selected_fields or None, {
        "value": requested,
        "display": (
            f"{requested} (custom field ID {field_id})"
            if requested is not None
            else f"blank (custom field ID {field_id})"
        ),
        "transport_field": "custom_fields",
        "custom_field_id": field_id,
    }


def validate_template(template: dict[str, Any]) -> dict[str, Any]:
    required_top_level = ["project_id", "tracker_id"]
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
        field_id = str(field.get("id"))
        if field_id in field_ids:
            raise RedmineMcpError(
                f"Duplicate Redmine custom field ID: {field_id}",
                code="TEMPLATE_INVALID",
            )
        keys.add(key)
        field_ids.add(field_id)
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
    severity_contract = _validate_severity_contract(template)
    priority_contract = _validate_priority_contract(template)
    text_format = normalize_text_format_contract(template.get("text_format"))
    _resolve_priority(
        template,
        custom_priority=None,
        custom_fields=raw_fields,
    )
    if template.get("priority_id") in (None, "") and (
        severity_contract is None or severity_contract.get("default") in (None, "")
    ):
        raise RedmineMcpError(
            "Redmine template requires priority_id or severity.default.",
            code="TEMPLATE_INVALID",
        )
    resolved_priority_id, severity_summary = _resolve_severity(
        template,
        severity=None,
        priority_id=None,
    )
    blocked = blocked_manager_fields(template)
    return {
        "project_id": str(template["project_id"]),
        "tracker_id": str(template["tracker_id"]),
        "priority_id": resolved_priority_id,
        "severity": severity_summary,
        "priority": priority_contract,
        "text_format": text_format,
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
    severity: str | None,
    custom_priority: Any,
    custom_fields: Any,
    category_id: str | None,
    assigned_to_id: str | None,
    fixed_version_id: str | None,
) -> dict[str, Any]:
    summary = validate_template(template) if template else {
        "unresolved_fields": [],
        "required_custom_fields": [],
        "text_format": None,
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
    resolved_priority_id, severity_summary = _resolve_severity(
        template,
        severity=severity,
        priority_id=priority_id,
    )
    resolved_custom_fields, priority_summary = _resolve_priority(
        template,
        custom_priority=custom_priority,
        custom_fields=selected_custom_fields,
    )
    return {
        "project_id": project_id or template.get("project_id"),
        "tracker_id": tracker_id or template.get("tracker_id"),
        "priority_id": resolved_priority_id,
        "custom_fields": resolved_custom_fields,
        "issue_fields": {
            "severity": severity_summary,
            "priority": priority_summary,
        },
        "text_format": summary.get("text_format"),
        "category_id": category_id if category_id is not None else template.get("category_id"),
        "assigned_to_id": assigned_to_id if assigned_to_id is not None else template.get("assigned_to_id"),
        "fixed_version_id": (
            fixed_version_id if fixed_version_id is not None else template.get("fixed_version_id")
        ),
        "template_summary": summary,
    }
