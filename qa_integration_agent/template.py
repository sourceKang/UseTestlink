from __future__ import annotations

import datetime as _datetime
import json
import os
import re
from pathlib import Path
from typing import Any

from .errors import CoordinatorError


_TOKEN_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _lookup(path: str, *, header: dict[str, str], result: dict[str, Any], context: dict[str, Any]) -> Any:
    path = path.strip()
    if path == "today":
        return _datetime.date.today().isoformat()
    if path == "report_date":
        generated = header.get("Report generated on", "")
        match = re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", generated)
        return match.group(0).replace("/", "-") if match else _datetime.date.today().isoformat()
    if path.startswith("env."):
        return os.environ.get(path[4:], "")
    if path.startswith("header."):
        return header.get(path[7:], "")
    if path.startswith("result."):
        return result.get(path[7:], "")
    if path.startswith("context."):
        value: Any = context
        for part in path[8:].split("."):
            value = value.get(part, "") if isinstance(value, dict) else ""
            if value in (None, ""):
                return ""
        return value
    return ""


def render_value(value: Any, *, header: dict[str, str], result: dict[str, Any], context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _TOKEN_RE.sub(
            lambda match: str(_lookup(match.group(1), header=header, result=result, context=context)),
            value,
        )
    if isinstance(value, list):
        return [render_value(item, header=header, result=result, context=context) for item in value]
    if isinstance(value, dict):
        return {key: render_value(item, header=header, result=result, context=context) for key, item in value.items()}
    return value


def _load_template(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    template_path = Path(path)
    if not template_path.exists():
        raise CoordinatorError(f"Redmine template does not exist: {template_path}", code="TEMPLATE_NOT_FOUND")
    try:
        value = json.loads(template_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CoordinatorError(f"Redmine template is not valid JSON: {template_path}", code="TEMPLATE_INVALID") from exc
    if not isinstance(value, dict):
        raise CoordinatorError("Redmine template must be a JSON object.", code="TEMPLATE_INVALID")
    return value


def _normalize_fields(value: Any) -> list[dict[str, Any]]:
    if value in (None, "", []):
        return []
    if isinstance(value, dict):
        rows: list[dict[str, Any]] = []
        for name, field_value in value.items():
            if isinstance(field_value, dict):
                row = dict(field_value)
                row.setdefault("name", str(name))
            else:
                row = {"id": int(name) if str(name).isdigit() else None, "name": str(name), "value": field_value}
            rows.append(row)
        return rows
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return [dict(item) for item in value]
    raise CoordinatorError("Redmine custom fields must be an object or array of objects.", code="TEMPLATE_INVALID")


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value or all(_blank(item) for item in value)
    return False


def render_custom_fields(
    *,
    template_file: str | None,
    custom_fields: Any,
    header: dict[str, str],
    result: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]] | None:
    template = _load_template(template_file)
    merged: dict[str, dict[str, Any]] = {}
    for source in (template.get("custom_fields"), custom_fields):
        for raw_field in _normalize_fields(source):
            field = render_value(raw_field, header=header, result=result, context=context)
            field_id = field.get("id")
            field_name = str(field.get("name") or "").strip()
            if field_id in (None, ""):
                raise CoordinatorError(
                    f"Redmine custom field requires an id: {field_name or field}",
                    code="TEMPLATE_INVALID",
                )
            key = f"id:{field_id}"
            merged[key] = field

    required = template.get("required_custom_fields") or []
    if not isinstance(required, list):
        raise CoordinatorError("required_custom_fields must be an array.", code="TEMPLATE_INVALID")
    missing: list[str] = []
    for raw_required in required:
        if isinstance(raw_required, dict):
            required_id = raw_required.get("id")
            required_name = str(raw_required.get("name") or required_id or "").strip()
        else:
            required_id = raw_required if str(raw_required).isdigit() else None
            required_name = str(raw_required).strip()
        matched = next(
            (
                field
                for field in merged.values()
                if (required_id not in (None, "") and str(field.get("id")) == str(required_id))
                or (required_name and str(field.get("name") or "").strip().casefold() == required_name.casefold())
            ),
            None,
        )
        if matched is None or _blank(matched.get("value")):
            missing.append(required_name or str(required_id))
    if missing:
        raise CoordinatorError("Missing required Redmine custom fields: " + ", ".join(missing), code="TEMPLATE_INVALID")

    rendered = [
        {"id": field["id"], "value": field.get("value")}
        for field in merged.values()
        if not _blank(field.get("value"))
    ]
    return rendered or None
