from __future__ import annotations

from typing import Any

from .common import schema, string


REPORT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "report_result",
        "description": "Preview or report one TestLink execution result.",
        "inputSchema": {
            **schema(
                {
                    "project": string("Exact TestLink project name. Use with plan when testplan_id is omitted."),
                    "plan": string("Exact TestLink test plan name. Use with project when testplan_id is omitted."),
                    "testplan_id": string("Internal TestLink test plan ID."),
                    "testcase_external_id": string("External testcase ID, for example GW-123."),
                    "testcase_id": string("Internal TestLink testcase ID."),
                    "build": string("Exact build name. Use when build_id is omitted."),
                    "build_id": string("Internal TestLink build ID."),
                    "platform": string("Exact platform name."),
                    "platform_id": string("Internal TestLink platform ID."),
                    "platformname": string("Platform name sent to reportTCResult."),
                    "status": {"type": "string", "enum": ["p", "f", "b"]},
                    "notes": {"type": "string"},
                    "framework": string("Automation framework name."),
                    "framework_name": string("Automation framework name."),
                    "executed_at": string("Automation execution time."),
                    "execution_time": string("Automation execution time."),
                    "failure_summary": string("Failure reason summary."),
                    "bug_id": string("Bug ID appended to notes as BUG-ID."),
                    "execution_duration": {"type": "number"},
                    "write": {"type": "boolean", "default": False},
                },
                ["status", "notes"],
            ),
            "allOf": [
                {"anyOf": [{"required": ["testcase_external_id"]}, {"required": ["testcase_id"]}]},
                {"anyOf": [{"required": ["testplan_id"]}, {"required": ["project", "plan"]}]},
                {"anyOf": [{"required": ["build_id"]}, {"required": ["build"]}]},
            ],
        },
    },
    {
        "name": "report_results_batch",
        "description": "Preview or report multiple TestLink execution results and return status statistics.",
        "inputSchema": {
            **schema(
                {
                    "project": string("Exact TestLink project name. Use with plan when testplan_id is omitted."),
                    "plan": string("Exact TestLink test plan name. Use with project when testplan_id is omitted."),
                    "testplan_id": string("Internal TestLink test plan ID."),
                    "build": string("Exact build name. Use when build_id is omitted."),
                    "build_id": string("Internal TestLink build ID."),
                    "platform": string("Exact platform name."),
                    "platform_id": string("Internal TestLink platform ID."),
                    "write": {"type": "boolean", "default": False},
                    "results": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "testcase_external_id": string("External testcase ID, for example GW-123."),
                                "testcase_id": string("Internal TestLink testcase ID."),
                                "status": {"type": "string", "enum": ["p", "f", "b"]},
                                "notes": {"type": "string"},
                                "platformname": string("Platform name sent to reportTCResult."),
                                "framework": string("Automation framework name."),
                                "framework_name": string("Automation framework name."),
                                "executed_at": string("Automation execution time."),
                                "execution_time": string("Automation execution time."),
                                "failure_summary": string("Failure reason summary."),
                                "bug_id": string("Bug ID appended to notes as BUG-ID."),
                                "execution_duration": {"type": "number"},
                            },
                            "required": ["status", "notes"],
                            "anyOf": [{"required": ["testcase_external_id"]}, {"required": ["testcase_id"]}],
                        },
                    },
                },
                ["results"],
            ),
            "allOf": [
                {"anyOf": [{"required": ["testplan_id"]}, {"required": ["project", "plan"]}]},
                {"anyOf": [{"required": ["build_id"]}, {"required": ["build"]}]},
            ],
        },
    },
    {
        "name": "testlink_upload_report",
        "description": "Preview or upload an automation report. Defaults to preview; set write true only after review.",
        "inputSchema": schema(
            {
                "project": string("Exact TestLink project name."),
                "plan": string("Exact test plan name."),
                "platform": string("Exact platform name."),
                "build": string("Build name. Omit with build_id to use latest active/open build."),
                "build_id": string("Build ID. Omit with build to use latest active/open build."),
                "report": string("Automation report file path."),
                "skip_policy": {"type": "string", "enum": ["ignore", "blocked"], "default": "ignore"},
                "write": {"type": "boolean", "default": False},
                "require_open_build": {"type": "boolean", "default": True},
                "redmine_create_bugs": {"type": "boolean", "default": False},
                "redmine_url": string("Redmine base URL. Defaults to REDMINE_URL."),
                "redmine_api_key": string("Redmine API key. Prefer REDMINE_API_KEY."),
                "redmine_project": string("Redmine project identifier or ID."),
                "redmine_tracker_id": string("Redmine tracker ID."),
                "redmine_status_id": string("Redmine status ID."),
                "redmine_priority_id": string("Redmine priority ID."),
                "redmine_assigned_to_id": string(
                    "Manager-only Redmine assignee ID. Requires REDMINE_ALLOW_MANAGER_FIELDS=true."
                ),
                "redmine_category_id": string("Redmine category ID."),
                "redmine_fixed_version_id": string(
                    "Manager-only Redmine target version ID. Requires REDMINE_ALLOW_MANAGER_FIELDS=true."
                ),
                "redmine_issue_id": string("Existing Redmine issue ID to record for failed results."),
                "redmine_issue_url": string("Existing Redmine issue URL to record in notes."),
                "redmine_dedupe": {"type": "string", "enum": ["open", "none"], "default": "open"},
                "testlink_bug_link": {"type": "string", "enum": ["bugid", "notes", "both"], "default": "notes"},
            },
            ["project", "plan", "platform", "report"],
        ),
    },
]
