from __future__ import annotations

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
    "redmine_priority_id": string("Redmine priority ID or use a template."),
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
        "name": "qa_preview_report_import",
        "description": "Build an aggregated TestLink/Redmine preview without external writes.",
        "inputSchema": schema(PLAN_PROPERTIES, PLAN_REQUIRED),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "qa_execute_report_import",
        "description": "Execute an unchanged confirmed QA import plan with item-level audit.",
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
        "description": "Resume only missing actions from a matching partial-failure audit.",
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
            {"operation_id": string("Workflow operation identity."), "audit_file": string("Audit JSON path.")},
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
