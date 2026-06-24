from __future__ import annotations

from typing import Any

from .common import PROFILES_PROPERTY, schema, string


MUTATE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "create_build",
        "description": "Preview or create a TestLink build after checking for duplicate build names.",
        "inputSchema": schema(
            {
                "project": string("Exact TestLink project name."),
                "plan": string("Exact TestLink test plan name."),
                "name": string("Build name."),
                "notes": {"type": "string", "default": ""},
                "active": {"type": "boolean", "default": True},
                "open": {"type": "boolean", "default": True},
                "releasedate": string("Optional release date accepted by TestLink."),
                "write": {"type": "boolean", "default": False},
            },
            ["project", "plan", "name"],
        ),
    },
    {
        "name": "create_test_case",
        "description": "Preview or create one TestLink test case after checking for duplicates in the target suite.",
        "inputSchema": {
            **schema(
                {
                    "project": string("Exact TestLink project name."),
                    "suite_name": string("Exact suite name or path."),
                    "suite_id": string("Suite ID."),
                    "name": string("Test case title."),
                    "author_login": string("TestLink author login. Defaults to TESTLINK_AUTHOR_LOGIN."),
                    "summary": {"type": "string", "default": ""},
                    "preconditions": {"type": "string", "default": ""},
                    "steps": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "importance": {"type": "string", "default": "medium"},
                    "execution_type": {"type": "string", "default": "manual"},
                    "order": {"type": "integer"},
                    "write": {"type": "boolean", "default": False},
                },
                ["project", "name", "steps"],
            ),
            "anyOf": [{"required": ["suite_name"]}, {"required": ["suite_id"]}],
        },
    },
    {
        "name": "update_test_case",
        "description": "Preview or update one TestLink test case.",
        "inputSchema": {
            **schema(
                {
                    "testcase_external_id": string("External testcase ID, for example GW-123."),
                    "testcase_id": string("Internal TestLink testcase ID."),
                    "version": string("Optional testcase version."),
                    "name": string("New test case title."),
                    "summary": string("New summary."),
                    "preconditions": string("New preconditions."),
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "importance": string("low, medium, high, or numeric TestLink value."),
                    "execution_type": string("manual, automated, or numeric TestLink value."),
                    "write": {"type": "boolean", "default": False},
                },
            ),
            "anyOf": [{"required": ["testcase_external_id"]}, {"required": ["testcase_id"]}],
        },
    },
    {
        "name": "add_case_to_plan",
        "description": "Preview or add a testcase version to a TestLink plan after checking for existing membership.",
        "inputSchema": {
            **schema(
                {
                    "project": string("Exact TestLink project name."),
                    "plan": string("Exact TestLink test plan name."),
                    "testcase_external_id": string("External testcase ID, for example GW-123."),
                    "testcase_id": string("Internal TestLink testcase ID."),
                    "version": string("Testcase version. Omit to resolve from TestLink when possible."),
                    "platform": string("Exact platform name."),
                    "platform_id": string("Internal platform ID."),
                    "execution_order": {"type": "integer"},
                    "urgency": string("TestLink urgency value."),
                    "write": {"type": "boolean", "default": False},
                },
                ["project", "plan"],
            ),
            "anyOf": [{"required": ["testcase_external_id"]}, {"required": ["testcase_id"]}],
        },
    },
    {
        "name": "upload_attachment",
        "description": "Preview or upload an attachment to a TestLink testcase, suite, project, or execution.",
        "inputSchema": schema(
            {
                "attachment_type": {"type": "string", "enum": ["testcase", "testsuite", "testproject", "execution"]},
                "target_id": string("Target TestLink entity ID."),
                "file": string("Local attachment file path."),
                "title": string("Attachment title. Defaults to filename."),
                "description": {"type": "string", "default": ""},
                "write": {"type": "boolean", "default": False},
            },
            ["attachment_type", "target_id", "file"],
        ),
    },
    {
        "name": "testlink_save_profile",
        "description": "Save a reusable local project/suite profile.",
        "inputSchema": schema(
            {
                "name": string("Profile name."),
                "project": string("Exact TestLink project name."),
                "suite_id": string("Target suite ID."),
                "suite_name": string("Exact suite name/path."),
                "project_contains": string("Search project names by substring."),
                "suite_contains": string("Search suite names or paths by substring."),
                "profiles": PROFILES_PROPERTY,
                "catalog": {"type": "string", "default": "local/testlink_catalog.json"},
                "active_only": {"type": "boolean", "default": True},
                "recursive": {"type": "boolean", "default": True},
                "max_projects": {"type": "integer", "default": 20, "minimum": 1},
                "refresh": {"type": "boolean", "default": False},
                "offline": {"type": "boolean", "default": False},
                "force": {"type": "boolean", "default": False},
            },
            ["name"],
        ),
    },
    {
        "name": "testlink_delete_profile",
        "description": "Delete a saved local project/suite profile.",
        "inputSchema": schema(
            {"name": string("Profile name."), "profiles": PROFILES_PROPERTY},
            ["name"],
        ),
        "annotations": {"destructiveHint": True, "requiresConfirmation": True},
    },
    {
        "name": "testlink_create_testcase",
        "description": "Preview or create one TestLink test case. Defaults to preview; set write true only after review.",
        "inputSchema": schema(
            {
                "profile": string("Saved local target profile."),
                "profiles": PROFILES_PROPERTY,
                "project": string("Exact TestLink project name."),
                "suite_id": string("Target suite ID."),
                "suite_name": string("Exact suite name/path."),
                "name": string("Test case title."),
                "author_login": string("TestLink author login. Defaults to TESTLINK_AUTHOR_LOGIN."),
                "summary": {"type": "string", "default": ""},
                "summary_file": string("UTF-8 file containing summary."),
                "preconditions": {"type": "string", "default": ""},
                "preconditions_file": string("UTF-8 file containing preconditions."),
                "steps": {"type": "array", "items": {"type": "string"}},
                "steps_file": string("JSON array of step strings or objects."),
                "importance": {"type": "string", "default": "medium"},
                "execution_type": {"type": "string", "default": "manual"},
                "order": {"type": "integer"},
                "duplicate_action": {"type": "string", "enum": ["block", "generate-new"], "default": "block"},
                "write": {"type": "boolean", "default": False},
            },
            ["name"],
        ),
    },
    {
        "name": "testlink_update_testcase",
        "description": "Preview or update one TestLink test case. Defaults to preview; set write true only after review.",
        "inputSchema": schema(
            {
                "profile": string("Saved local target profile."),
                "profiles": PROFILES_PROPERTY,
                "project": string("Exact TestLink project name for preview context."),
                "suite_id": string("Target suite ID for preview context."),
                "suite_name": string("Exact suite name/path for preview context."),
                "testcase_id": string("Internal TestLink testcase ID."),
                "testcase_external_id": string("External testcase ID, for example GW-123."),
                "version": string("Optional testcase version."),
                "name": string("New test case title."),
                "summary": string("New summary."),
                "summary_file": string("UTF-8 file containing new summary."),
                "preconditions": string("New preconditions."),
                "preconditions_file": string("UTF-8 file containing new preconditions."),
                "steps": {"type": "array", "items": {"type": "string"}},
                "steps_file": string("JSON array of replacement steps."),
                "importance": string("low, medium, high, or numeric TestLink value."),
                "execution_type": string("manual, automated, or numeric TestLink value."),
                "write": {"type": "boolean", "default": False},
            },
        ),
    },
]
