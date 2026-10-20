from __future__ import annotations

import os
from typing import Any

from qa_mcp_contracts.toolsets import resolve_tool_names
from testlink_agent_core.tools import TOOLS as LEGACY_TOOLS


EXCLUDED_LEGACY_TOOLS = {
    "create_build",
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
            "operation_id": string("Stable ID for audit and resume."),
            "environment": {"type": "string", "enum": ["corp", "sandbox"]},
            "project": string("Exact name."),
            "plan": string("Exact name."),
            "testcase_external_id": string("External testcase ID."),
            "build": string("Exact name."),
            "build_id": string("Internal build ID."),
            "platform": string("Exact name."),
            "platform_id": string("Internal platform ID."),
            "status": {"type": "string", "enum": ["p", "f", "b"]},
            "notes": {"type": "string"},
            "execution_duration": {"type": "number", "minimum": 0},
            "write": {"type": "boolean", "default": False},
            "preview_digest": string("Digest from the reviewed preview."),
            "audit_dir": string("Writable absolute audit directory."),
            "env_file": {"type": "string"},
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
        "operation_id": string("Stable ID for audit and resume."),
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
        "preview_digest": string("Digest from the reviewed preview."),
        "audit_dir": string("Writable absolute audit directory."),
        "env_file": {"type": "string"},
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
            "project": string("Exact name."),
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
            "testcase_external_id": string("External ID, e.g. GW-123."),
            "testcase_id": string("Internal ID."),
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
            "operation_id": string("Stable ID for this lookup."),
            "environment": {"type": "string", "enum": ["corp", "sandbox"]},
            "project": string("Exact name."),
            "plan": string("Exact name."),
            "platform": string("Exact name."),
            "build": string("Exact name."),
            "testcase_external_id": string("Optional exact testcase external ID in the selected target."),
            "env_file": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "default": 60},
        },
        "required": ["operation_id", "environment", "project", "plan", "platform", "build"],
    },
    "annotations": {"readOnlyHint": True},
}


LINK_BUG_TOOL: dict[str, Any] = {
    "name": "testlink_link_bug",
    "description": (
        "Preview or link Redmine bug IDs to one testcase via the corporate bug-link endpoint. "
        "Always appends an execution; the first link per build adds a Redmine note; links are not "
        "confirmed. Write needs preview_digest."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operation_id": string("Stable ID for audit and resume."),
            "environment": {"type": "string", "enum": ["corp", "sandbox"]},
            "project": string("Exact name."),
            "plan": string("Exact name."),
            "platform": string("Exact name."),
            "build": string("Exact name."),
            "testcase_external_id": string("Plain external ID such as PRJ-1234 (no _N suffix)."),
            "bug_ids": {
                "type": "array",
                "items": {"type": "string", "pattern": "^[1-9][0-9]{0,9}$"},
                "minItems": 1,
                "maxItems": 20,
                "description": "Redmine issue IDs.",
            },
            "status": {
                "type": "string",
                "enum": ["p", "f", "b"],
                "description": "Result written with the link; normally f.",
            },
            "notes": {"type": "string", "description": "Execution notes."},
            "execution_duration": {
                "type": "integer",
                "minimum": 0,
                "default": 5,
                "description": "Minutes; default 5.",
            },
            "write": {"type": "boolean", "default": False},
            "preview_digest": string("Digest from the reviewed preview."),
            "audit_dir": string("Writable absolute audit directory."),
            "env_file": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "default": 60},
        },
        "required": [
            "operation_id",
            "environment",
            "project",
            "plan",
            "platform",
            "build",
            "testcase_external_id",
            "bug_ids",
            "status",
        ],
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
}


CREATE_BUILD_TOOL: dict[str, Any] = {
    "name": "testlink_create_build",
    "description": (
        "Preview or create one build in an exact project/plan. Refuses an existing name; "
        "write needs preview_digest and is verified by readback."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operation_id": string("Stable ID for audit and resume."),
            "environment": {"type": "string", "enum": ["corp", "sandbox"]},
            "project": string("Exact name."),
            "plan": string("Exact name."),
            "name": string("New build name; no surrounding spaces."),
            "notes": {"type": "string", "description": "Build notes."},
            "release_date": string("YYYY-MM-DD."),
            "active": {"type": "boolean", "default": True},
            "open": {"type": "boolean", "default": True},
            "write": {"type": "boolean", "default": False},
            "preview_digest": string("Digest from the reviewed preview."),
            "audit_dir": string("Writable absolute audit directory."),
            "env_file": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "default": 60},
        },
        "required": ["operation_id", "environment", "project", "plan", "name"],
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
}


LATEST_RESULTS_TOOL: dict[str, Any] = {
    "name": "testlink_get_latest_results",
    "description": (
        "Read the latest execution of many testcases on one exact build/platform in one call. "
        "With expected_status, only rows that differ are listed. Status n = not run."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operation_id": string("Stable ID for this lookup."),
            "environment": {"type": "string", "enum": ["corp", "sandbox"]},
            "project": string("Exact name."),
            "plan": string("Exact name."),
            "platform": string("Exact name."),
            "build": string("Exact name."),
            "testcase_external_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 1000,
            },
            "all_in_plan": {
                "type": "boolean",
                "default": False,
                "description": "Read every testcase in the plan/platform instead of a list.",
            },
            "expected_status": {"type": "string", "enum": ["p", "f", "b"]},
            "env_file": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "default": 60},
        },
        "required": ["operation_id", "environment", "project", "plan", "platform", "build"],
    },
    "annotations": {"readOnlyHint": True},
}


TOOLS = [
    RESOLVE_EXECUTION_TARGET_TOOL,
    LATEST_RESULTS_TOOL,
    REPORT_EXECUTION_TOOL,
    LINK_BUG_TOOL,
    CREATE_BUILD_TOOL,
    CREATE_TESTCASE_TOOL,
    UPDATE_TESTCASE_TOOL,
    *(tool for tool in LEGACY_TOOLS if tool["name"] not in EXCLUDED_LEGACY_TOOLS),
]


TOOLSET_ENV = "TESTLINK_MCP_TOOLSET"
# An unset toolset must not expose write tools or the legacy delete/create tools that
# "all" carries; write surfaces (execution, maintenance) and "all" are explicit opt-ins.
DEFAULT_TOOLSET = "discovery"
TOOLSETS: dict[str, set[str]] = {
    "integration": {"testlink_report_execution"},
    "execution": {
        "testlink_resolve_execution_target",
        "testlink_get_latest_results",
        "testlink_report_execution",
        "testlink_link_bug",
        "testlink_create_build",
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
    # The read side of "execution" without the legacy duplicates and catalog tools in "discovery".
    "lookup": {
        "testlink_resolve_execution_target",
        "testlink_get_latest_results",
        "testlink_list_projects",
        "testlink_list_plans",
        "testlink_list_platforms",
        "testlink_list_builds",
        "list_test_cases",
        "get_test_case",
        "get_last_result",
    },
    "all": {tool["name"] for tool in TOOLS},
}
# Usage-profile presets shared by name with redmine-mcp; see docs/claude-setup.md.
PRESETS: dict[str, tuple[str, ...]] = {
    "batch-import": ("execution", "maintenance"),
    "manual-qa": ("execution",),
    "bug-tracking": ("lookup",),
}


def tools_for_toolset(toolset: str | None = None) -> list[dict[str, Any]]:
    allowed = resolve_tool_names(
        toolset or os.environ.get(TOOLSET_ENV, DEFAULT_TOOLSET),
        toolsets=TOOLSETS,
        presets=PRESETS,
        env_name=TOOLSET_ENV,
    )
    return [tool for tool in TOOLS if tool["name"] in allowed]
