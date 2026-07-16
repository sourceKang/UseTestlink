from __future__ import annotations

from typing import Any

from testlink_agent_core.tools import TOOLS as LEGACY_TOOLS


EXCLUDED_LEGACY_TOOLS = {
    "link_bug",
    "overwrite_result",
    "report_result",
    "report_results_batch",
    "testlink_upload_report",
}


def string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


REPORT_EXECUTION_TOOL: dict[str, Any] = {
    "name": "testlink_report_execution",
    "description": "Preview or append one TestLink execution using a confirmed preview digest.",
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operation_id": string("Caller-generated operation identity used for audit and resume."),
            "environment": {"type": "string", "enum": ["corp", "sandbox"]},
            "project": string("Exact TestLink project name."),
            "plan": string("Exact TestLink test plan name."),
            "testcase_external_id": string("External testcase ID."),
            "build": string("Exact TestLink build name."),
            "build_id": string("Internal build ID."),
            "platform": string("Exact TestLink platform name."),
            "platform_id": string("Internal platform ID."),
            "status": {"type": "string", "enum": ["p", "f", "b"]},
            "notes": {"type": "string"},
            "execution_duration": {"type": "number", "minimum": 0},
            "write": {"type": "boolean", "default": False},
            "preview_digest": string("Digest returned by the reviewed preview."),
            "audit_dir": string("Local directory for redacted TestLink operation audit."),
            "env_file": string("Optional TestLink env file path."),
            "timeout": {"type": "integer", "minimum": 1, "default": 60},
        },
        "required": [
            "operation_id",
            "environment",
            "project",
            "plan",
            "platform",
            "testcase_external_id",
            "status",
            "notes"
        ],
        "allOf": [
            {"anyOf": [{"required": ["build_id"]}, {"required": ["build"]}]},
        ],
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
}


TOOLS = [
    REPORT_EXECUTION_TOOL,
    *(tool for tool in LEGACY_TOOLS if tool["name"] not in EXCLUDED_LEGACY_TOOLS),
]
