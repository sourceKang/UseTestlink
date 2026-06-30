from __future__ import annotations

import argparse
import json
import sys
import xmlrpc.client

from .commands import (
    command_create_testcase,
    command_delete_profile,
    command_download_testcases,
    command_find_suites,
    command_list_builds,
    command_list_platforms,
    command_list_plans,
    command_list_profiles,
    command_list_projects,
    command_list_suites,
    command_refresh_catalog,
    command_save_profile,
    command_update_testcase,
    command_upload_report,
)
from .config import DEFAULT_CATALOG_PATH, DEFAULT_PROFILES_PATH, DEFAULT_TIMEOUT_SECONDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TestLink XML-RPC helper.")
    parser.add_argument(
        "--env-file",
        help=(
            "Optional env file. Defaults to TESTLINK_AGENT_ENV_FILE, .env, "
            "or local/testlink_agent.env when present."
        ),
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_projects = subparsers.add_parser("list-projects", help="List visible TestLink projects.")
    list_projects.set_defaults(func=command_list_projects)

    list_plans = subparsers.add_parser("list-plans", help="List test plans for a project.")
    list_plans.add_argument("--project", required=True)
    list_plans.set_defaults(func=command_list_plans)

    list_platforms = subparsers.add_parser("list-platforms", help="List platforms for a test plan.")
    list_platforms.add_argument("--project", required=True)
    list_platforms.add_argument("--plan", required=True)
    list_platforms.set_defaults(func=command_list_platforms)

    list_builds = subparsers.add_parser("list-builds", help="List builds for a test plan.")
    list_builds.add_argument("--project", required=True)
    list_builds.add_argument("--plan", required=True)
    list_builds.add_argument("--open-only", action="store_true", help="Show only active/open builds.")
    list_builds.set_defaults(func=command_list_builds)

    list_suites = subparsers.add_parser("list-suites", help="List test suites for a project.")
    list_suites.add_argument("--project", required=True)
    list_suites.add_argument("--parent-suite-id", help="List child suites under this suite ID.")
    list_suites.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="List nested suites recursively. Defaults to true.",
    )
    list_suites.set_defaults(func=command_list_suites)

    find_suites = subparsers.add_parser("find-suites", help="Search projects and suites, then print create-testcase args.")
    find_suites.add_argument("--project-contains", help="Filter project names by partial text.")
    find_suites.add_argument("--suite-contains", help="Filter suite names or paths by partial text.")
    find_suites.add_argument(
        "--active-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Search active projects only. Defaults to true.",
    )
    find_suites.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Search nested suites recursively. Defaults to true.",
    )
    find_suites.add_argument(
        "--max-projects",
        type=int,
        default=20,
        help="Maximum projects to scan after project filtering. Defaults to 20.",
    )
    find_suites.add_argument("--catalog", default=DEFAULT_CATALOG_PATH, help="Local catalog path.")
    find_suites.add_argument("--refresh", action="store_true", help="Refresh the local catalog before searching.")
    find_suites.add_argument("--offline", action="store_true", help="Use only the local catalog; do not connect to TestLink.")
    find_suites.set_defaults(func=command_find_suites)

    refresh_catalog = subparsers.add_parser("refresh-catalog", help="Download project/suite catalog for faster local search.")
    refresh_catalog.add_argument("--out", default=DEFAULT_CATALOG_PATH, help="Catalog output path.")
    refresh_catalog.add_argument("--project-contains", help="Refresh only matching project names.")
    refresh_catalog.add_argument(
        "--active-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Refresh active projects only. Defaults to true.",
    )
    refresh_catalog.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Download nested suites recursively. Defaults to true.",
    )
    refresh_catalog.add_argument(
        "--max-projects",
        type=int,
        default=20,
        help="Maximum projects to refresh after project filtering. Defaults to 20.",
    )
    refresh_catalog.add_argument("--force", action="store_true", help="Overwrite existing catalog.")
    refresh_catalog.set_defaults(func=command_refresh_catalog)

    save_profile = subparsers.add_parser("save-profile", help="Save a reusable project/suite target profile.")
    save_profile.add_argument("--name", required=True, help="Profile name, for example gateway-vpn.")
    save_profile.add_argument("--project", help="Exact TestLink project name.")
    save_profile.add_argument("--suite-id", help="Target TestLink test suite ID.")
    save_profile.add_argument("--suite-name", help="Exact target test suite name or path.")
    save_profile.add_argument("--project-contains", help="Search project names by partial text.")
    save_profile.add_argument("--suite-contains", help="Search suite names or paths by partial text.")
    save_profile.add_argument(
        "--active-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Search active projects only. Defaults to true.",
    )
    save_profile.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Search nested suites recursively. Defaults to true.",
    )
    save_profile.add_argument(
        "--max-projects",
        type=int,
        default=20,
        help="Maximum projects to scan after project filtering. Defaults to 20.",
    )
    save_profile.add_argument("--catalog", default=DEFAULT_CATALOG_PATH, help="Local catalog path.")
    save_profile.add_argument("--profiles", default=DEFAULT_PROFILES_PATH, help="Local profiles path.")
    save_profile.add_argument("--refresh", action="store_true", help="Refresh the local catalog before searching.")
    save_profile.add_argument("--offline", action="store_true", help="Use only the local catalog; do not connect to TestLink.")
    save_profile.add_argument("--force", action="store_true", help="Overwrite an existing profile.")
    save_profile.set_defaults(func=command_save_profile)

    list_profiles = subparsers.add_parser("list-profiles", help="List saved project/suite target profiles.")
    list_profiles.add_argument("--profiles", default=DEFAULT_PROFILES_PATH, help="Local profiles path.")
    list_profiles.set_defaults(func=command_list_profiles)

    delete_profile = subparsers.add_parser("delete-profile", help="Delete a saved target profile.")
    delete_profile.add_argument("--name", required=True)
    delete_profile.add_argument("--profiles", default=DEFAULT_PROFILES_PATH, help="Local profiles path.")
    delete_profile.set_defaults(func=command_delete_profile)

    download = subparsers.add_parser("download-testcases", help="Download test cases for a test plan and platform.")
    download.add_argument("--project", required=True)
    download.add_argument("--plan", required=True)
    download.add_argument("--platform", required=True)
    download.add_argument("--details", choices=("simple", "full"), default="simple")
    download.add_argument("--format", choices=("auto", "json", "xlsx"), default="auto")
    download.add_argument("--out", help="Write output to this file. Defaults to stdout for JSON.")
    download.add_argument("--force", action="store_true", help="Overwrite --out if it already exists.")
    download.set_defaults(func=command_download_testcases)

    create = subparsers.add_parser("create-testcase", help="Preview or create one TestLink test case.")
    create.add_argument("--profile", help="Saved target profile from local/testlink_profiles.json.")
    create.add_argument("--profiles", default=DEFAULT_PROFILES_PATH, help="Local profiles path.")
    create.add_argument("--project")
    suite_group = create.add_mutually_exclusive_group(required=False)
    suite_group.add_argument("--suite-id", help="Target TestLink test suite ID.")
    suite_group.add_argument("--suite-name", help="Exact target test suite name or path from list-suites.")
    create.add_argument("--name", required=True, help="Test case title.")
    create.add_argument("--author-login", help="TestLink author login. Defaults to TESTLINK_AUTHOR_LOGIN.")
    summary_group = create.add_mutually_exclusive_group(required=False)
    summary_group.add_argument("--summary", default="", help="Test case summary.")
    summary_group.add_argument("--summary-file", help="Read test case summary from a UTF-8 text file.")
    preconditions_group = create.add_mutually_exclusive_group(required=False)
    preconditions_group.add_argument("--preconditions", default="", help="Test case preconditions.")
    preconditions_group.add_argument("--preconditions-file", help="Read preconditions from a UTF-8 text file.")
    create.add_argument(
        "--step",
        action="append",
        help='Add a step as "actions => expected results". May be repeated.',
    )
    create.add_argument(
        "--steps-file",
        help='Read steps from a JSON array. Items may be strings or objects with "actions" and "expected_results".',
    )
    create.add_argument(
        "--single-step",
        action="store_true",
        help="Collapse all supplied steps into one TestLink step row with numbered action/result lines.",
    )
    create.add_argument("--importance", default="medium", help="low, medium, high, or a numeric TestLink value.")
    create.add_argument("--execution-type", default="manual", help="manual, automated, or a numeric TestLink value.")
    create.add_argument("--order", type=int, help="Optional display order inside the target suite.")
    create.add_argument(
        "--duplicate-action",
        choices=("block", "generate-new"),
        default="block",
        help="How TestLink should handle an existing test case name in the suite.",
    )
    create.add_argument("--write", action="store_true", help="Actually create the test case. Omit for preview only.")
    create.set_defaults(func=command_create_testcase)

    update = subparsers.add_parser("update-testcase", help="Preview or update one TestLink test case.")
    update.add_argument("--profile", help="Saved target profile for preview context.")
    update.add_argument("--profiles", default=DEFAULT_PROFILES_PATH, help="Local profiles path.")
    update.add_argument("--project", help="Exact TestLink project name, used for preview context and suite lookup.")
    update_suite_group = update.add_mutually_exclusive_group(required=False)
    update_suite_group.add_argument("--suite-id", help="Target TestLink test suite ID for preview context.")
    update_suite_group.add_argument("--suite-name", help="Exact target test suite name or path from list-suites.")
    update_case_group = update.add_mutually_exclusive_group(required=True)
    update_case_group.add_argument("--testcase-id", help="Internal TestLink testcase ID.")
    update_case_group.add_argument("--testcase-external-id", help='External testcase ID, for example "GW-123".')
    update.add_argument("--version", help="Optional testcase version to update.")
    update.add_argument("--name", help="New test case title.")
    update_summary_group = update.add_mutually_exclusive_group(required=False)
    update_summary_group.add_argument("--summary", help="New test case summary.")
    update_summary_group.add_argument("--summary-file", help="Read new test case summary from a UTF-8 text file.")
    update_preconditions_group = update.add_mutually_exclusive_group(required=False)
    update_preconditions_group.add_argument("--preconditions", help="New test case preconditions.")
    update_preconditions_group.add_argument("--preconditions-file", help="Read new preconditions from a UTF-8 text file.")
    update.add_argument(
        "--step",
        action="append",
        help='Replace steps with this step as "actions => expected results". May be repeated.',
    )
    update.add_argument(
        "--steps-file",
        help='Replace steps from a JSON array. Items may be strings or objects with "actions" and "expected_results".',
    )
    update.add_argument(
        "--single-step",
        action="store_true",
        help="Collapse all supplied replacement steps into one TestLink step row with numbered action/result lines.",
    )
    update.add_argument("--importance", help="low, medium, high, or a numeric TestLink value.")
    update.add_argument("--execution-type", help="manual, automated, or a numeric TestLink value.")
    update.add_argument("--write", action="store_true", help="Actually update the test case. Omit for preview only.")
    update.set_defaults(func=command_update_testcase)

    upload = subparsers.add_parser("upload-report", help="Preview or upload an automation report.")
    upload.add_argument("--project", required=True)
    upload.add_argument("--plan", required=True)
    upload.add_argument("--platform", required=True)
    build_group = upload.add_mutually_exclusive_group(required=False)
    build_group.add_argument("--build", help="Build name. Omit with --build-id to use the latest active/open build.")
    build_group.add_argument("--build-id", help="Build ID. Omit with --build to use the latest active/open build.")
    upload.add_argument("--report", required=True)
    upload.add_argument("--skip-policy", choices=("ignore", "blocked"), default="ignore")
    upload.add_argument("--write", action="store_true", help="Actually write results. Omit for preview only.")
    upload.add_argument("--require-open-build", action="store_true", default=True)
    upload.add_argument("--progress", type=int, default=25)
    upload.add_argument("--throttle", type=float, default=0.03)
    upload.add_argument("--redmine-create-bugs", action="store_true", help="Create or reuse Redmine issues for failed results.")
    upload.add_argument("--redmine-url", help="Redmine base URL. Defaults to REDMINE_URL.")
    upload.add_argument("--redmine-api-key", help="Redmine API key. Prefer REDMINE_API_KEY.")
    upload.add_argument("--redmine-project", help="Redmine project identifier or ID. Defaults to REDMINE_PROJECT_ID.")
    upload.add_argument("--redmine-template", help="Redmine project template JSON file. Defaults to REDMINE_TEMPLATE.")
    upload.add_argument(
        "--redmine-custom-field",
        dest="redmine_custom_fields",
        action="append",
        help='Override one Redmine custom field as "custom field id=value" or "name=value". May be repeated.',
    )
    upload.add_argument("--redmine-tracker-id", help="Redmine tracker ID. Defaults to REDMINE_TRACKER_ID.")
    upload.add_argument("--redmine-status-id", help="Redmine status ID for new issues. Defaults to REDMINE_STATUS_ID.")
    upload.add_argument("--redmine-priority-id", help="Redmine priority ID. Defaults to REDMINE_PRIORITY_ID.")
    upload.add_argument(
        "--redmine-assigned-to-id",
        help="Manager-only Redmine assignee ID. Requires REDMINE_ALLOW_MANAGER_FIELDS=true.",
    )
    upload.add_argument("--redmine-category-id", help="Redmine category ID. Defaults to REDMINE_CATEGORY_ID.")
    upload.add_argument(
        "--redmine-fixed-version-id",
        help="Manager-only Redmine target version ID. Requires REDMINE_ALLOW_MANAGER_FIELDS=true.",
    )
    upload.add_argument("--redmine-issue-id", help="Existing Redmine issue ID to link for failed results without using Redmine API.")
    upload.add_argument("--redmine-issue-url", help="Existing Redmine issue URL to record in notes.")
    upload.add_argument(
        "--redmine-dedupe",
        choices=("open", "none"),
        default="open",
        help="Reuse an open Redmine issue with the same subject before creating a new one.",
    )
    upload.add_argument(
        "--testlink-bug-link",
        choices=("bugid", "notes", "both"),
        default="notes",
        help="How Redmine issue IDs are sent to TestLink. Notes always include Redmine metadata.",
    )
    upload.set_defaults(func=command_upload_report)

    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except xmlrpc.client.Fault as fault:
        print(
            json.dumps(
                {"error": "TestLinkFault", "code": fault.faultCode, "message": fault.faultString},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
