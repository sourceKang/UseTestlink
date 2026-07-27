from __future__ import annotations

import os
from typing import Any

from testlink_agent_core.tools import TOOLS as LEGACY_TOOLS


EXCLUDED_LEGACY_TOOLS = {
    "create_test_case",
    "link_bug",
    "overwrite_result",
    "report_result",
    "report_results_batch",
    "testlink_upload_report",
    "testlink_create_testcase",
    "testlink_update_testcase",
    "update_test_case",
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


def testcase_row_policy_schema() -> list[dict[str, Any]]:
    return [
        {
            "if": {"properties": {"single_step": {"const": False}}, "required": ["single_step"]},
            "then": {
                "properties": {"allow_multi_row": {"const": True}},
                "required": ["allow_multi_row"],
            },
        },
        {
            "if": {"properties": {"allow_multi_row": {"const": True}}, "required": ["allow_multi_row"]},
            "then": {"properties": {"single_step": {"const": False}}, "required": ["single_step"]},
        },
    ]


def protected_write_properties() -> dict[str, Any]:
    return {
        "operation_id": string("Caller-generated operation identity used for audit and resume."),
        "environment": {"type": "string", "enum": ["corp", "sandbox"]},
        "single_step": {
            "type": "boolean",
            "default": True,
            "description": "Collapse all logical steps into one TestLink row.",
        },
        "allow_multi_row": {
            "type": "boolean",
            "default": False,
            "description": "Explicit multi-row authorization; valid only with single_step=false.",
        },
        "write": {"type": "boolean", "default": False},
        "preview_digest": string("Digest returned by the reviewed preview."),
        "audit_dir": string("Local directory for redacted TestLink operation audit."),
        "env_file": string("Optional TestLink env file path."),
        "timeout": {"type": "integer", "minimum": 1, "default": 60},
    }


CREATE_TESTCASE_TOOL: dict[str, Any] = {
    "name": "testlink_create_testcase",
    "description": "Preview or create one TestLink testcase with protected row policy and readback verification.",
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **protected_write_properties(),
            "project": string("Exact TestLink project name."),
            "suite_name": string("Exact TestLink suite name or path."),
            "suite_id": string("Internal TestLink suite ID."),
            "name": string("Testcase title."),
            "author_login": string("TestLink author login. Defaults to TESTLINK_AUTHOR_LOGIN."),
            "summary": {"type": "string", "default": ""},
            "preconditions": {"type": "string", "default": ""},
            "steps": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "importance": {"type": "string", "default": "medium"},
            "execution_type": {"type": "string", "default": "manual"},
            "order": {"type": "integer"},
        },
        "required": ["operation_id", "environment", "project", "name", "steps"],
        "allOf": [
            {"anyOf": [{"required": ["suite_name"]}, {"required": ["suite_id"]}]},
            *testcase_row_policy_schema(),
        ],
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
}


UPDATE_TESTCASE_TOOL: dict[str, Any] = {
    "name": "testlink_update_testcase",
    "description": "Preview or update one TestLink testcase with protected row policy and readback verification.",
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            **protected_write_properties(),
            "testcase_external_id": string("External testcase ID, for example GW-123."),
            "testcase_id": string("Internal TestLink testcase ID."),
            "version": string("Optional testcase version."),
            "name": string("New testcase title."),
            "summary": string("New summary."),
            "preconditions": string("New preconditions."),
            "steps": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "importance": string("low, medium, high, or numeric TestLink value."),
            "execution_type": string("manual, automated, or numeric TestLink value."),
        },
        "required": ["operation_id", "environment"],
        "allOf": [
            {"anyOf": [{"required": ["testcase_external_id"]}, {"required": ["testcase_id"]}]},
            *testcase_row_policy_schema(),
        ],
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
}


RESOLVE_EXECUTION_TARGET_TOOL: dict[str, Any] = {
    "name": "testlink_resolve_execution_target",
    "description": "Resolve one exact project/plan/platform/build/testcase target in a single read-only call.",
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operation_id": string("Caller-generated discovery operation identity."),
            "environment": {"type": "string", "enum": ["corp", "sandbox"]},
            "project": string("Exact TestLink project name."),
            "plan": string("Exact TestLink plan name."),
            "platform": string("Exact TestLink platform name."),
            "build": string("Exact TestLink build name."),
            "testcase_external_id": string("Optional exact testcase external ID in the selected target."),
            "env_file": string("Optional TestLink env file path."),
            "timeout": {"type": "integer", "minimum": 1, "default": 60},
        },
        "required": ["operation_id", "environment", "project", "plan", "platform", "build"],
    },
    "annotations": {"readOnlyHint": True},
}


TOOLS = [
    RESOLVE_EXECUTION_TARGET_TOOL,
    REPORT_EXECUTION_TOOL,
    CREATE_TESTCASE_TOOL,
    UPDATE_TESTCASE_TOOL,
    *(tool for tool in LEGACY_TOOLS if tool["name"] not in EXCLUDED_LEGACY_TOOLS),
]


TOOLSET_ENV = "TESTLINK_MCP_TOOLSET"
TOOLSETS: dict[str, set[str]] = {
    "integration": {"testlink_report_execution"},
    "execution": {
        "testlink_resolve_execution_target",
        "testlink_report_execution",
        "testlink_list_projects",
        "testlink_list_plans",
        "testlink_list_platforms",
        "testlink_list_builds",
        "list_test_cases",
        "get_test_case",
        "get_last_result",
    },
    "maintenance": {
        "testlink_resolve_execution_target",
        "testlink_create_testcase",
        "testlink_update_testcase",
        "testlink_list_projects",
        "testlink_list_suites",
        "testlink_find_suites",
        "list_test_cases",
        "get_test_case",
    },
    "discovery": {
        tool["name"]
        for tool in TOOLS
        if bool(tool.get("annotations", {}).get("readOnlyHint"))
    },
    "all": {tool["name"] for tool in TOOLS},
}


def tools_for_toolset(toolset: str | None = None) -> list[dict[str, Any]]:
    selected = str(toolset or os.environ.get(TOOLSET_ENV, "all")).strip().casefold()
    if selected not in TOOLSETS:
        choices = ", ".join(sorted(TOOLSETS))
        raise ValueError(f"{TOOLSET_ENV} must be one of: {choices}.")
    allowed = TOOLSETS[selected]
    return [tool for tool in TOOLS if tool["name"] in allowed]
