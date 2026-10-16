from __future__ import annotations

import os
from typing import Any

from qa_mcp_contracts.toolsets import resolve_tool_names


def string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


COMMON_PROPERTIES: dict[str, Any] = {
    "operation_id": string("Caller-generated operation identity used for audit and resume."),
    "environment": {"type": "string", "enum": ["corp", "sandbox"]},
    "env_file": string("Optional Redmine MCP env file path."),
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
    "project_id": string("Redmine project identifier. Defaults to REDMINE_PROJECT_ID."),
    "template_file": string("Optional validated Redmine project template JSON."),
    "subject": string("Bug subject."),
    "description": string("Bug description including external evidence."),
    "tracker_id": string("Redmine tracker ID."),
    "priority_id": string("Legacy Redmine priority ID; validated against severity mapping when configured."),
    "severity": string("Semantic Severity label resolved through the validated template mapping."),
    "custom_priority": string(
        "Value for the distinct custom Priority field; rejected until template allowed values are confirmed."
    ),
    "custom_fields": {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {}, "value": {}},
            "required": ["id", "value"],
        },
    },
    "category_id": string("Optional Redmine category ID."),
    "assigned_to_id": string("Manager-only assignee ID; blocked unless explicitly enabled."),
    "fixed_version_id": string("Manager-only fixed version ID; blocked unless explicitly enabled."),
    "dedupe_marker": string("Stable marker used to find and reuse an existing issue."),
    "dedupe": {"type": "string", "enum": ["open"], "default": "open"},
    "attachments": {
        "type": "array",
        "maxItems": 5,
        "description": (
            "Optional local image files attached only when a new bug is created. "
            "Each image is limited to 10 MiB and preview-bound by SHA-256."
        ),
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "file": string("Local PNG, JPEG, GIF, WebP, or BMP image path."),
                "filename": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Optional attachment filename; defaults to the local basename.",
                },
                "description": {
                    "type": "string",
                    "maxLength": 255,
                    "description": "Optional Redmine attachment description.",
                },
            },
            "required": ["file"],
        },
    },
    "audit_dir": string("Local directory for redacted operation audit JSON."),
}


BUG_REQUIRED = [
    "operation_id",
    "environment",
    "subject",
    "description",
    "dedupe_marker",
]


COMMENT_PROPERTIES: dict[str, Any] = {
    "issue_id": string("Existing Redmine issue ID."),
    "notes": string("Evidence comment; does not alter status, assignee, or fixed version."),
    "template_file": string("Optional validated Redmine project template JSON."),
    "audit_dir": string("Local directory for redacted operation audit JSON."),
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
            "Search Redmine issues and return safe summary fields. Custom field filters are verified "
            "against every returned issue; if Redmine ignored a filter the call fails with "
            "FILTER_NOT_APPLIED instead of returning unfiltered results."
        ),
        "inputSchema": schema(
            {
                "project_id": string("Redmine project identifier. Defaults to REDMINE_PROJECT_ID."),
                "status_id": string(
                    "Redmine status filter: open, closed, *, or a status ID. open means every status "
                    "whose Redmine is_closed flag is false, so it can include Resolved; pass a specific "
                    "status ID to narrow further."
                ),
                "tracker_id": string("Optional tracker ID."),
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
                    "description": "Numeric custom field IDs whose values are returned for each issue.",
                },
                "author_id": string("Author user ID, or me."),
                "assigned_to_id": string("Assignee user ID, or me."),
                "category_id": string("Redmine issue category ID."),
                "updated_from": string("Inclusive YYYY-MM-DD lower bound on updated_on."),
                "updated_to": string("Inclusive YYYY-MM-DD upper bound on updated_on."),
                "closed_from": string("Inclusive YYYY-MM-DD lower bound on closed_on."),
                "closed_to": string("Inclusive YYYY-MM-DD upper bound on closed_on."),
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
            "Read full Redmine issue content: description, status, custom fields, "
            "and journal/comment history. Excludes watchers and other internal fields; "
            "embedded credentials and email local parts are masked."
        ),
        "inputSchema": schema(
            {"issue_id": string("Existing Redmine issue ID to read.")},
            ["operation_id", "environment", "issue_id"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_list_projects",
        "description": (
            "List all Redmine projects (paginated server-side) with identifier and name so callers "
            "can resolve project_id instead of guessing."
        ),
        "inputSchema": schema(
            {"query": string("Optional case-insensitive substring matched against name or identifier.")},
            ["operation_id", "environment"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "redmine_get_project_metadata",
        "description": "Read safe Redmine project, tracker, priority, custom-field, and status metadata.",
        "inputSchema": schema(
            {"project_id": string("Redmine project identifier. Defaults to REDMINE_PROJECT_ID.")},
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
                "preview_digest": string("Digest returned by the reviewed preview."),
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
                "preview_digest": string("Digest returned by the reviewed preview."),
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
