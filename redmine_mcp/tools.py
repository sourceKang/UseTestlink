from __future__ import annotations

import os
from typing import Any

from qa_mcp_contracts.toolsets import resolve_tool_names


def string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


COMMON_PROPERTIES: dict[str, Any] = {
    "operation_id": string("Stable ID for audit and resume."),
    "environment": {"type": "string", "enum": ["corp", "sandbox"]},
    "env_file": {"type": "string"},
    "timeout": {"type": "integer", "minimum": 1, "default": 60},
}


def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {**COMMON_PROPERTIES, **properties},
        "required": required,
    }


BUG_PROPERTIES: dict[str, Any] = {
    "project_id": string("Project identifier; defaults to REDMINE_PROJECT_ID."),
    "template_file": string("Validated project template JSON."),
    "subject": string("Bug subject."),
    "description": string("Bug description with evidence."),
    "tracker_id": string("Redmine tracker ID."),
    "priority_id": string("Legacy priority ID; checked against the severity mapping."),
    "severity": string("Severity label mapped by the template."),
    "custom_priority": string("Custom Priority value; rejected until template values are confirmed."),
    "custom_fields": {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {}, "value": {}},
            "required": ["id", "value"],
        },
    },
    "category_id": string("Category ID."),
    "assigned_to_id": string("Manager-only; blocked unless enabled."),
    "fixed_version_id": string("Manager-only; blocked unless enabled."),
    "dedupe_marker": string("Stable marker to find and reuse an existing issue."),
    "dedupe": {"type": "string", "enum": ["open"], "default": "open"},
    "attachments": {
        "type": "array",
        "maxItems": 5,
        "description": "Local images (max 10 MiB each), attached only to a new bug; bound to the preview by SHA-256.",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "file": string("Local PNG/JPEG/GIF/WebP/BMP path."),
                "filename": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Defaults to the file basename.",
                },
                "description": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Attachment description.",
                },
            },
            "required": ["file"],
        },
    },
    "audit_dir": string("Writable absolute audit directory."),
}


BUG_REQUIRED = [
    "operation_id",
    "environment",
    "subject",
    "description",
    "dedupe_marker",
]


COMMENT_PROPERTIES: dict[str, Any] = {
    "issue_id": string("Issue ID."),
    "notes": string("Evidence comment; never changes status, assignee, or version."),
    "template_file": string("Validated project template JSON."),
    "audit_dir": string("Writable absolute audit directory."),
}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "redmine_health",
        "description": "Verify Redmine API authentication without exposing credentials.",
        "inputSchema": schema({}, ["operation_id", "environment"]),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_search_issues",
        "description": (
            "Search issues; returns safe summaries. Custom field filters are verified on every result "
            "(FILTER_NOT_APPLIED if Redmine ignored one)."
        ),
        "inputSchema": schema(
            {
                "project_id": string("Project identifier; defaults to REDMINE_PROJECT_ID."),
                "status_id": string("open, closed, *, or a status ID. open = not is_closed, so it includes Resolved."),
                "tracker_id": string("Tracker ID."),
                "subject_contains": string("Case-insensitive subject keyword."),
                "custom_field_filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": string("Numeric custom field ID."),
                            "value": string("Value to match."),
                            "match": {"type": "string", "enum": ["exact", "contains"], "default": "exact"},
                        },
                        "required": ["id", "value"],
                    },
                },
                "include_custom_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Custom field IDs to return per issue.",
                },
                "author_id": string("Author user ID, or me."),
                "assigned_to_id": string("Assignee user ID, or me."),
                "category_id": string("Category ID."),
                "updated_from": string("updated_on >= YYYY-MM-DD."),
                "updated_to": string("updated_on <= YYYY-MM-DD."),
                "closed_from": string("closed_on >= YYYY-MM-DD."),
                "closed_to": string("closed_on <= YYYY-MM-DD."),
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
            },
            ["operation_id", "environment"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_get_issue",
        "description": (
            "Read an issue's description, status, custom fields, and journals; "
            "credentials and email local parts are masked."
        ),
        "inputSchema": schema(
            {"issue_id": string("Issue ID.")},
            ["operation_id", "environment", "issue_id"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_list_projects",
        "description": (
            "List projects (identifier, name) to resolve project_id instead of guessing."
        ),
        "inputSchema": schema(
            {"query": string("Substring of name or identifier.")},
            ["operation_id", "environment"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_get_project_metadata",
        "description": "Read safe Redmine project, tracker, priority, custom-field, and status metadata.",
        "inputSchema": schema(
            {"project_id": string("Project identifier; defaults to REDMINE_PROJECT_ID.")},
            ["operation_id", "environment"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_validate_template",
        "description": "Validate a local Redmine project template without writing to Redmine.",
        "inputSchema": schema(
            {"template_file": string("Local Redmine template JSON path.")},
            ["operation_id", "environment", "template_file"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_preview_bug",
        "description": "Preview Redmine bug dedupe and policy decisions; never writes.",
        "inputSchema": schema(BUG_PROPERTIES, BUG_REQUIRED),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_create_bug",
        "description": "Preview by default or create/reuse a Redmine bug after confirmed preview digest.",
        "inputSchema": schema(
            {
                **BUG_PROPERTIES,
                "write": {"type": "boolean", "default": False},
                "preview_digest": string("Digest from the reviewed preview."),
            },
            BUG_REQUIRED,
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "redmine_preview_comment",
        "description": "Preview an evidence comment without writing.",
        "inputSchema": schema(COMMENT_PROPERTIES, ["operation_id", "environment", "issue_id", "notes"]),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_add_comment",
        "description": "Preview by default or add an evidence-only comment after confirmed preview digest.",
        "inputSchema": schema(
            {
                **COMMENT_PROPERTIES,
                "write": {"type": "boolean", "default": False},
                "preview_digest": string("Digest from the reviewed preview."),
            },
            ["operation_id", "environment", "issue_id", "notes"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    },
]


TOOLSET_ENV = "REDMINE_MCP_TOOLSET"
# An unset toolset exposes only reads; bug creation and comments ("issue",
# "integration", "all") are explicit opt-ins.
DEFAULT_TOOLSET = "discovery"
TOOLSETS: dict[str, set[str]] = {
    "discovery": {
        "redmine_search_issues",
        "redmine_get_issue",
        "redmine_list_projects",
        "redmine_health",
        "redmine_get_project_metadata",
        "redmine_validate_template",
    },
    "integration": {"redmine_create_bug", "redmine_add_comment"},
    "issue": {
        "redmine_search_issues",
        "redmine_get_issue",
        "redmine_list_projects",
        "redmine_preview_bug",
        "redmine_create_bug",
        "redmine_preview_comment",
        "redmine_add_comment",
    },
    "metadata": {
        "redmine_health",
        "redmine_list_projects",
        "redmine_get_project_metadata",
        "redmine_validate_template",
    },
    "all": {tool["name"] for tool in TOOLS},
}
# Usage-profile presets shared by name with testlink-mcp; see docs/claude-setup.md.
# batch-import files Redmine bugs through qa-integration-agent, so it only reads here.
PRESETS: dict[str, tuple[str, ...]] = {
    "batch-import": ("discovery",),
    "manual-qa": ("issue",),
    "bug-tracking": ("discovery",),
}


def tools_for_toolset(toolset: str | None = None) -> list[dict[str, Any]]:
    allowed = resolve_tool_names(
        toolset or os.environ.get(TOOLSET_ENV, DEFAULT_TOOLSET),
        toolsets=TOOLSETS,
        presets=PRESETS,
        env_name=TOOLSET_ENV,
    )
    return [tool for tool in TOOLS if tool["name"] in allowed]
