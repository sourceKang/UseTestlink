from __future__ import annotations

from typing import Any

from ..config import DEFAULT_CATALOG_PATH, DEFAULT_PROFILES_PATH, DEFAULT_TIMEOUT_SECONDS


JSON_OBJECT: dict[str, Any] = {"type": "object", "additionalProperties": False}
COMMON_PROPERTIES: dict[str, Any] = {
    "env_file": {"type": "string", "description": "Optional env file path."},
    "timeout": {"type": "integer", "default": DEFAULT_TIMEOUT_SECONDS, "minimum": 1},
}
READ_ONLY = {"readOnlyHint": True}
DESTRUCTIVE = {"destructiveHint": True, "requiresConfirmation": True}


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        **JSON_OBJECT,
        "properties": {**COMMON_PROPERTIES, **properties},
        "required": required or [],
    }


def local_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        **JSON_OBJECT,
        "properties": properties,
        "required": required or [],
    }


def string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


CATALOG_PROPERTY = {"type": "string", "default": DEFAULT_CATALOG_PATH}
PROFILES_PROPERTY = {"type": "string", "default": DEFAULT_PROFILES_PATH}
