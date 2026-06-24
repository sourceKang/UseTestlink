from __future__ import annotations

from typing import Any

from .common import CATALOG_PROPERTY, PROFILES_PROPERTY, READ_ONLY, schema, string


QUERY_TOOLS: list[dict[str, Any]] = [
    {
        "name": "find_project",
        "description": "Find one TestLink project by exact name and return close suggestions when not found.",
        "inputSchema": schema({"name": string("Exact TestLink project name.")}, ["name"]),
        "annotations": READ_ONLY,
    },
    {
        "name": "find_test_plan",
        "description": "Find one TestLink test plan by project name and exact plan name.",
        "inputSchema": schema(
            {
                "project": string("Exact TestLink project name."),
                "name": string("Exact TestLink test plan name."),
            },
            ["project", "name"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "list_test_suites",
        "description": "List TestLink test suites in a project using project and optional parent suite names.",
        "inputSchema": schema(
            {
                "project": string("Exact TestLink project name."),
                "parent_suite_id": string("Optional parent suite ID."),
                "parent_suite_name": string("Optional exact parent suite name or path."),
                "recursive": {"type": "boolean", "default": True},
            },
            ["project"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "list_test_cases",
        "description": "List TestLink test cases under a suite by suite name/path or suite ID.",
        "inputSchema": {
            **schema(
                {
                    "project": string("Exact TestLink project name."),
                    "suite_name": string("Exact suite name or path."),
                    "suite_id": string("Suite ID."),
                    "recursive": {"type": "boolean", "default": True},
                    "details": {"type": "string", "enum": ["simple", "full"], "default": "full"},
                },
                ["project"],
            ),
            "anyOf": [{"required": ["suite_name"]}, {"required": ["suite_id"]}],
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "get_test_case",
        "description": "Get full TestLink test case content by external ID or internal testcase ID.",
        "inputSchema": {
            **schema(
                {
                    "testcase_external_id": string("External testcase ID, for example GW-123."),
                    "testcase_id": string("Internal TestLink testcase ID."),
                    "version": string("Optional testcase version."),
                },
            ),
            "anyOf": [{"required": ["testcase_external_id"]}, {"required": ["testcase_id"]}],
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "get_last_result",
        "description": "Get the latest execution result for a testcase in a test plan.",
        "inputSchema": {
            **schema(
                {
                    "project": string("Exact TestLink project name."),
                    "plan": string("Exact TestLink test plan name."),
                    "testcase_external_id": string("External testcase ID, for example GW-123."),
                    "testcase_id": string("Internal TestLink testcase ID."),
                    "build": string("Optional exact build name."),
                    "build_id": string("Optional build ID."),
                    "platform": string("Optional exact platform name."),
                    "platform_id": string("Optional platform ID."),
                },
                ["project", "plan"],
            ),
            "anyOf": [{"required": ["testcase_external_id"]}, {"required": ["testcase_id"]}],
        },
        "annotations": READ_ONLY,
    },
    {
        "name": "get_builds",
        "description": "List builds for a project and test plan, newest first.",
        "inputSchema": schema(
            {
                "project": string("Exact TestLink project name."),
                "plan": string("Exact TestLink test plan name."),
                "open_only": {"type": "boolean", "default": False},
            },
            ["project", "plan"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_about",
        "description": "Return TestLink MCP version and live TestLink health details.",
        "inputSchema": schema({}),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_list_projects",
        "description": "List visible TestLink projects.",
        "inputSchema": schema({}),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_list_plans",
        "description": "List test plans for a TestLink project.",
        "inputSchema": schema({"project": string("Exact TestLink project name.")}, ["project"]),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_list_platforms",
        "description": "List platforms for a project and test plan.",
        "inputSchema": schema(
            {"project": string("Exact TestLink project name."), "plan": string("Exact test plan name.")},
            ["project", "plan"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_list_builds",
        "description": "List builds for a project and test plan, newest first.",
        "inputSchema": schema(
            {
                "project": string("Exact TestLink project name."),
                "plan": string("Exact test plan name."),
                "open_only": {"type": "boolean", "default": False},
            },
            ["project", "plan"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_list_suites",
        "description": "List test suites for a project.",
        "inputSchema": schema(
            {
                "project": string("Exact TestLink project name."),
                "parent_suite_id": string("Optional parent suite ID."),
                "recursive": {"type": "boolean", "default": True},
            },
            ["project"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_find_suites",
        "description": "Search projects and suites using a local catalog or TestLink refresh.",
        "inputSchema": schema(
            {
                "project_contains": string("Optional project name substring."),
                "suite_contains": string("Optional suite name/path substring."),
                "active_only": {"type": "boolean", "default": True},
                "recursive": {"type": "boolean", "default": True},
                "max_projects": {"type": "integer", "default": 20, "minimum": 1},
                "catalog": CATALOG_PROPERTY,
                "refresh": {"type": "boolean", "default": False},
                "offline": {"type": "boolean", "default": False},
            },
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_refresh_catalog",
        "description": "Download project and suite catalog for faster local search.",
        "inputSchema": schema(
            {
                "out": CATALOG_PROPERTY,
                "project_contains": string("Optional project name substring."),
                "active_only": {"type": "boolean", "default": True},
                "recursive": {"type": "boolean", "default": True},
                "max_projects": {"type": "integer", "default": 20, "minimum": 1},
                "force": {"type": "boolean", "default": False},
            },
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_download_testcases",
        "description": "Download test cases for a project, plan, and platform as JSON or XLSX.",
        "inputSchema": schema(
            {
                "project": string("Exact TestLink project name."),
                "plan": string("Exact test plan name."),
                "platform": string("Exact platform name."),
                "details": {"type": "string", "enum": ["simple", "full"], "default": "simple"},
                "format": {"type": "string", "enum": ["auto", "json", "xlsx"], "default": "auto"},
                "out": string("Optional output file path. JSON prints to result when omitted."),
                "force": {"type": "boolean", "default": False},
            },
            ["project", "plan", "platform"],
        ),
        "annotations": READ_ONLY,
    },
    {
        "name": "testlink_list_profiles",
        "description": "List saved local project/suite profiles.",
        "inputSchema": schema({"profiles": PROFILES_PROPERTY}),
        "annotations": READ_ONLY,
    },
]
