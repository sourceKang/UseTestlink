from __future__ import annotations

import os
from typing import Any


def string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


PLAN_PROPERTIES: dict[str, Any] = {
    "operation_id": string("Caller-generated workflow operation identity."),
    "correlation_id": string("Optional correlation identity; defaults to operation_id."),
    "environment": {"type": "string", "enum": ["corp", "sandbox"]},
    "project": string("Exact TestLink project name."),
    "plan": string("Exact TestLink plan name."),
    "platform": string("Exact TestLink platform name; never guessed."),
    "build": string("Exact TestLink build name."),
    "report": string("Local automation report path."),
    "skip_policy": {"type": "string", "enum": ["ignore", "blocked"], "default": "ignore"},
    "redmine_create_bugs": {"type": "boolean", "default": False},
    "redmine_project_id": string("Redmine project identifier; required when bug creation is enabled."),
    "redmine_tracker_id": string("Redmine tracker ID or use a template."),
    "redmine_priority_id": string(
        "Legacy Redmine priority ID; validated against the template Severity mapping when configured."
    ),
    "redmine_severity": string("Semantic Severity label resolved by redmine-mcp through the template mapping."),
    "redmine_custom_priority": string(
        "Value for the separate custom Priority field; leave blank until allowed values are confirmed."
    ),
    "redmine_template_file": string("Validated Redmine project template path."),
    "redmine_custom_fields": {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"id": {}, "value": {}},
            "required": ["id", "value"],
        },
    },
    "artifact_dir": string("Local directory for the exact persisted preview artifact."),
}


PLAN_REQUIRED = ["operation_id", "environment", "project", "plan", "platform", "build", "report"]


def schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "qa_preview_report_artifact",
        "description": "Persist an exact QA plan/review artifact and return a bounded preview summary.",
        "inputSchema": schema(PLAN_PROPERTIES, PLAN_REQUIRED),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "qa_preview_report_import",
        "description": "Compatibility v1 aggregate preview with inline per-item details.",
        "inputSchema": schema(PLAN_PROPERTIES, PLAN_REQUIRED),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "qa_execute_preview_artifact",
        "description": "Execute the exact confirmed persisted QA preview with item-level audit.",
        "inputSchema": schema(
            {
                "operation_id": string("Workflow operation identity from preview."),
                "preview_artifact": string("Exact local preview artifact returned by preview."),
                "preview_digest": string("Digest from the reviewed aggregate preview."),
                "write": {"type": "boolean", "const": True},
                "audit_dir": string("Local directory for workflow audit JSON."),
            },
            ["operation_id", "preview_artifact", "preview_digest", "write"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "qa_resume_preview_artifact",
        "description": "Resume only missing actions from a matching partial-failure audit.",
        "inputSchema": schema(
            {
                "operation_id": string("Workflow operation identity from preview."),
                "preview_artifact": string("Exact local preview artifact returned by preview."),
                "audit_file": string("Previous workflow audit JSON."),
                "write": {"type": "boolean", "const": True},
            },
            ["operation_id", "preview_artifact", "audit_file", "write"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "qa_execute_report_import",
        "description": "Compatibility v1 execution using unchanged repeated preview arguments.",
        "inputSchema": schema(
            {
                **PLAN_PROPERTIES,
                "preview_digest": string("Digest from the reviewed aggregate preview."),
                "write": {"type": "boolean", "const": True},
                "audit_dir": string("Local directory for workflow audit JSON."),
            },
            [*PLAN_REQUIRED, "preview_digest", "write"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "qa_resume_report_import",
        "description": "Compatibility v1 resume using unchanged repeated preview arguments.",
        "inputSchema": schema(
            {
                **PLAN_PROPERTIES,
                "audit_file": string("Previous workflow audit JSON."),
                "write": {"type": "boolean", "const": True},
            },
            [*PLAN_REQUIRED, "audit_file", "write"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "qa_get_operation",
        "description": "Read a redacted local workflow audit.",
        "inputSchema": schema(
            {
                "operation_id": string("Workflow operation identity."),
                "audit_file": string("Audit JSON path."),
                "include_details": {
                    "type": "boolean",
                    "default": False,
                    "description": "Explicitly return the full audit instead of the compact summary.",
                },
            },
            ["operation_id", "audit_file"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "qa_validate_traceability",
        "description": "Validate cross-system traceability from a workflow audit.",
        "inputSchema": schema(
            {"operation_id": string("Workflow operation identity."), "audit_file": string("Audit JSON path.")},
            ["operation_id", "audit_file"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "qa_compare_shadow_previews",
        "description": "Compare legacy and modern preview JSON without external writes.",
        "inputSchema": schema(
            {
                "operation_id": string("Comparison operation identity."),
                "legacy_preview_file": string("Legacy testlink_upload_report preview JSON."),
                "modern_preview_file": string("qa_preview_report_import preview JSON."),
            },
            ["operation_id", "legacy_preview_file", "modern_preview_file"],
        ),
        "annotations": {"readOnlyHint": True},
    },
]


TOOLSET_ENV = "QA_INTEGRATION_TOOLSET"
TOOLSETS: dict[str, set[str]] = {
    "import": {
        "qa_preview_report_artifact",
        "qa_execute_preview_artifact",
        "qa_resume_preview_artifact",
        "qa_get_operation",
        "qa_validate_traceability",
    },
    "legacy": {
        "qa_preview_report_import",
        "qa_execute_report_import",
        "qa_resume_report_import",
        "qa_get_operation",
        "qa_validate_traceability",
    },
    "shadow": {"qa_preview_report_import", "qa_compare_shadow_previews"},
    "all": {tool["name"] for tool in TOOLS},
}


def tools_for_toolset(toolset: str | None = None) -> list[dict[str, Any]]:
    selected = str(toolset or os.environ.get(TOOLSET_ENV, "import")).strip().casefold()
    if selected not in TOOLSETS:
        choices = ", ".join(sorted(TOOLSETS))
        raise ValueError(f"{TOOLSET_ENV} must be one of: {choices}.")
    allowed = TOOLSETS[selected]
    return [tool for tool in TOOLS if tool["name"] in allowed]
