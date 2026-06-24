from __future__ import annotations

import argparse
import json
import os
import time
import xmlrpc.client
from collections import Counter
from pathlib import Path
from typing import Any

from .catalog import build_catalog, find_suites_in_catalog, read_catalog, write_catalog
from .client import TestLinkClient
from .config import DEFAULT_CATALOG_PATH, catalog_path, load_testlink_settings
from .errors import TestLinkError
from .models import RedmineIssue
from .output import write_json_output, write_xlsx_output
from .profiles import (
    apply_create_profile,
    delete_profile,
    list_profiles,
    profile_from_suite_search_row,
    profile_from_values,
    profiles_path,
    save_profile,
)
from .redmine import (
    RedmineClient,
    build_existing_redmine_issue,
    build_notes,
    build_redmine_issue_payload,
    manager_fields_enabled,
    redmine_arg,
    redmine_issue_to_dict,
)
from .reports import map_results_to_plan, parse_report, result_to_dict
from .suites import collect_test_suites, resolve_suite_by_name
from .testcases import create_testcase_payload, flatten_plan_cases, normalize_testcase, update_testcase_payload


def parse_common_env(args: argparse.Namespace) -> TestLinkClient:
    settings = load_testlink_settings(env_file=args.env_file, timeout=args.timeout)
    client = TestLinkClient(settings.url, settings.devkey, timeout=settings.timeout)
    if not client.check_devkey():
        raise TestLinkError("tl.checkDevKey failed.")
    return client

def parse_redmine_client(args: argparse.Namespace) -> RedmineClient:
    base_url = redmine_arg(args, "redmine_url", "REDMINE_URL")
    api_key = redmine_arg(args, "redmine_api_key", "REDMINE_API_KEY")
    return RedmineClient(base_url, api_key, timeout=args.timeout)

def resolve_context(args: argparse.Namespace, client: TestLinkClient) -> dict[str, Any]:
    project = client.get_project_by_name(args.project)
    plan = client.get_test_plan_by_name(args.project, args.plan)
    platform = client.get_platform_by_name(str(plan["id"]), args.platform)
    build = client.get_build(str(plan["id"]), args.build, args.build_id)

    if args.require_open_build and (str(build.get("active")) != "1" or str(build.get("is_open")) != "1"):
        raise TestLinkError(f"Build is not active/open: {build.get('name')} ({build.get('id')})")

    return {
        "project": project,
        "plan": plan,
        "platform": platform,
        "build": build,
    }

def command_upload_report(args: argparse.Namespace) -> int:
    if args.redmine_create_bugs and args.redmine_issue_id:
        raise TestLinkError("--redmine-create-bugs and --redmine-issue-id cannot be used together.")

    report_path = Path(args.report)
    if not report_path.exists():
        raise TestLinkError(f"Report file does not exist: {report_path}")

    header, parsed = parse_report(report_path)
    if args.skip_policy == "blocked":
        for result in parsed:
            if result.raw_status.lower() in ("skip", "skipped"):
                result.status = "b"
    elif args.skip_policy == "ignore":
        pass
    else:
        raise TestLinkError(f"Unsupported skip policy: {args.skip_policy}")

    writable = [result for result in parsed if result.status in ("p", "f", "b")]
    ignored = [result for result in parsed if result.status is None]
    duplicate_ids = sorted([eid for eid, count in Counter(result.external_id for result in parsed).items() if count > 1])
    if duplicate_ids:
        raise TestLinkError(f"Duplicate testcase ids in report: {', '.join(duplicate_ids)}")

    client = parse_common_env(args)
    context = resolve_context(args, client)

    plan_id = str(context["plan"]["id"])
    platform_id = str(context["platform"]["id"])
    build_id = str(context["build"]["id"])
    plan_cases = client.get_plan_cases_by_external_id(plan_id, platform_id)
    missing = map_results_to_plan(writable, plan_cases)
    if missing:
        raise TestLinkError(f"Test cases not found in plan/platform: {', '.join(sorted(missing))}")

    redmine_bug_preview: list[dict[str, Any]] = []
    if args.redmine_create_bugs or args.redmine_issue_id:
        for result in writable:
            if result.status != "f":
                continue
            existing_issue = build_existing_redmine_issue(args, result)
            if existing_issue is not None:
                redmine_bug_preview.append(
                    {
                        "external_id": result.external_id,
                        "test_name": result.test_name,
                        "issue_id": existing_issue.id,
                        "issue_url": existing_issue.url,
                        "action": "link-existing",
                    }
                )
            else:
                issue_payload = build_redmine_issue_payload(args, header, report_path, result, context)
                redmine_bug_preview.append(
                    {
                        "external_id": result.external_id,
                        "test_name": result.test_name,
                        "subject": issue_payload["subject"],
                        "project_id": issue_payload["project_id"],
                        "tracker_id": issue_payload.get("tracker_id"),
                        "priority_id": issue_payload.get("priority_id"),
                        "action": "create-or-reuse",
                    }
                )

    preview = {
        "mode": "write" if args.write else "preview",
        "report": str(report_path),
        "target": {
            "project": args.project,
            "testplan": context["plan"].get("name"),
            "testplanid": plan_id,
            "platform": context["platform"].get("name"),
            "platformid": platform_id,
            "build": context["build"].get("name"),
            "buildid": build_id,
        },
        "parsed_count": len(parsed),
        "write_count": len(writable),
        "ignored_count": len(ignored),
        "raw_status_counts": dict(Counter(result.raw_status for result in parsed)),
        "write_status_counts": dict(Counter(result.status for result in writable)),
        "failures_to_write": [result_to_dict(result) for result in writable if result.status == "f"],
        "ignored": [result_to_dict(result) for result in ignored],
        "redmine": {
            "enabled": args.redmine_create_bugs or bool(args.redmine_issue_id),
            "dedupe": args.redmine_dedupe,
            "testlink_bug_link": args.testlink_bug_link,
            "manager_fields_enabled": manager_fields_enabled(),
            "issues_to_create_or_reuse": redmine_bug_preview,
        },
    }

    if not args.write:
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        return 0

    redmine_client = parse_redmine_client(args) if args.redmine_create_bugs else None
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, result in enumerate(writable, start=1):
        redmine_issue: RedmineIssue | None = None
        if result.status == "f":
            redmine_issue = build_existing_redmine_issue(args, result)
        if redmine_client is not None and result.status == "f" and redmine_issue is None:
            try:
                issue_payload = build_redmine_issue_payload(args, header, report_path, result, context)
                if args.redmine_dedupe == "open":
                    redmine_issue = redmine_client.find_open_issue_by_subject(
                        str(issue_payload["project_id"]),
                        str(issue_payload["subject"]),
                        str(issue_payload.get("tracker_id") or ""),
                    )
                if redmine_issue is None:
                    redmine_issue = redmine_client.create_issue(issue_payload)
            except Exception as exc:
                failures.append(
                    {
                        "external_id": result.external_id,
                        "status": result.status,
                        "stage": "redmine",
                        "error": str(exc),
                    }
                )
                continue

        params: dict[str, Any] = {
            "testcaseexternalid": result.external_id,
            "testplanid": plan_id,
            "buildid": build_id,
            "platformid": platform_id,
            "platformname": context["platform"].get("name"),
            "status": result.status,
            "notes": build_notes(header, report_path, result, redmine_issue),
        }
        if redmine_issue is not None and args.testlink_bug_link in ("bugid", "both"):
            params["bugid"] = redmine_issue.id
        if result.duration_seconds is not None:
            params["execduration"] = round(result.duration_seconds / 60.0, 4)

        try:
            response = client.report_result(params)
            successes.append(
                {
                    "external_id": result.external_id,
                    "status": result.status,
                    "redmine_issue": redmine_issue_to_dict(redmine_issue),
                    "response": response,
                }
            )
        except xmlrpc.client.Fault as fault:
            failures.append(
                {
                    "external_id": result.external_id,
                    "status": result.status,
                    "stage": "testlink",
                    "redmine_issue": redmine_issue_to_dict(redmine_issue),
                    "fault_code": fault.faultCode,
                    "fault_message": fault.faultString,
                }
            )
        except Exception as exc:
            failures.append(
                {
                    "external_id": result.external_id,
                    "status": result.status,
                    "stage": "testlink",
                    "redmine_issue": redmine_issue_to_dict(redmine_issue),
                    "error": str(exc),
                }
            )

        if args.progress and index % args.progress == 0:
            print(
                json.dumps(
                    {"progress": index, "success": len(successes), "failed": len(failures)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
        time.sleep(args.throttle)

    output = {
        **preview,
        "success_count": len(successes),
        "failure_count": len(failures),
        "success_status_counts": dict(Counter(item["status"] for item in successes)),
        "failure_status_counts": dict(Counter(item["status"] for item in failures)),
        "failures": failures,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    return 0 if not failures else 2

def command_download_testcases(args: argparse.Namespace) -> int:
    client = parse_common_env(args)
    project = client.get_project_by_name(args.project)
    plan = client.get_test_plan_by_name(args.project, args.plan)
    platform = client.get_platform_by_name(str(plan["id"]), args.platform)

    raw_cases = client.get_plan_cases(str(plan["id"]), str(platform["id"]), details=args.details)
    testcases = [normalize_testcase(case) for case in flatten_plan_cases(raw_cases)]
    testcases = sorted(
        testcases,
        key=lambda case: (
            str(case.get("execution_order") or ""),
            str(case.get("external_id") or ""),
            str(case.get("name") or ""),
        ),
    )
    payload = {
        "target": {
            "project": project.get("name") or args.project,
            "testprojectid": project.get("id"),
            "testplan": plan.get("name") or args.plan,
            "testplanid": plan.get("id"),
            "platform": platform.get("name") or args.platform,
            "platformid": platform.get("id"),
        },
        "details": args.details,
        "case_count": len(testcases),
        "testcases": testcases,
    }
    if args.format == "xlsx" or (args.format == "auto" and args.out and args.out.lower().endswith(".xlsx")):
        write_xlsx_output(testcases, args.out, force=args.force)
    else:
        write_json_output(payload, args.out, force=args.force)
    return 0

def command_create_testcase(args: argparse.Namespace) -> int:
    profile = apply_create_profile(args)
    if not args.project:
        raise TestLinkError("--project or --profile is required.")
    if not args.suite_id and not args.suite_name:
        raise TestLinkError("--suite-id, --suite-name, or --profile is required.")

    client = parse_common_env(args)
    project = client.get_project_by_name(args.project)
    suite: dict[str, Any] | None = None
    suite_id = args.suite_id
    if args.suite_name:
        suites = collect_test_suites(client, str(project["id"]), recursive=True)
        suite = resolve_suite_by_name(suites, args.suite_name)
        suite_id = str(suite["id"])
    payload = create_testcase_payload(args, project, suite_id=suite_id)
    preview = {
        "mode": "write" if args.write else "preview",
        "target": {
            "project": project.get("name") or args.project,
            "testprojectid": project.get("id"),
            "testsuiteid": suite_id,
            "testsuite": suite.get("name") if suite else None,
            "testsuite_path": suite.get("path") if suite else None,
            "profile": args.profile,
            "profile_target": profile,
        },
        "testcase": {
            "name": payload["testcasename"],
            "authorlogin": payload["authorlogin"],
            "summary": payload["summary"],
            "preconditions": payload.get("preconditions", ""),
            "importance": payload["importance"],
            "executiontype": payload["executiontype"],
            "order": payload.get("order"),
            "duplicate_action": args.duplicate_action,
            "steps": payload["steps"],
        },
    }
    if not args.write:
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        return 0

    response = client.create_test_case(payload)
    print(json.dumps({**preview, "response": response}, indent=2, ensure_ascii=False, default=str))
    return 0

def command_update_testcase(args: argparse.Namespace) -> int:
    profile = apply_create_profile(args)
    client = parse_common_env(args)

    project: dict[str, Any] | None = None
    suite: dict[str, Any] | None = None
    suite_id = args.suite_id
    if args.project:
        project = client.get_project_by_name(args.project)
        if args.suite_name:
            suites = collect_test_suites(client, str(project["id"]), recursive=True)
            suite = resolve_suite_by_name(suites, args.suite_name)
            suite_id = str(suite["id"])

    payload = update_testcase_payload(args)
    preview = {
        "mode": "write" if args.write else "preview",
        "target": {
            "profile": args.profile,
            "profile_target": profile,
            "project": project.get("name") if project else args.project,
            "testprojectid": project.get("id") if project else None,
            "testsuiteid": suite_id,
            "testsuite": suite.get("name") if suite else None,
            "testsuite_path": suite.get("path") if suite else None,
            "testcaseid": payload.get("testcaseid"),
            "testcaseexternalid": payload.get("testcaseexternalid"),
            "version": payload.get("version"),
        },
        "updates": {
            key: value
            for key, value in payload.items()
            if key not in {"testcaseid", "testcaseexternalid", "version"}
        },
    }
    if not args.write:
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        return 0

    response = client.update_test_case(payload)
    print(json.dumps({**preview, "response": response}, indent=2, ensure_ascii=False, default=str))
    return 0

def command_save_profile(args: argparse.Namespace) -> int:
    path = profiles_path(args.profiles)
    profile: dict[str, Any]
    source = "direct"

    if args.project and args.suite_id:
        profile = profile_from_values(
            project=args.project,
            suite_id=args.suite_id,
            suite_name=args.suite_name,
        )
    elif args.project and args.suite_name:
        client = parse_common_env(args)
        project = client.get_project_by_name(args.project)
        suites = collect_test_suites(client, str(project["id"]), recursive=True)
        suite = resolve_suite_by_name(suites, args.suite_name)
        profile = profile_from_values(
            project=str(project.get("name") or args.project),
            suite_id=str(suite["id"]),
            suite_name=str(suite.get("name") or ""),
            suite_path=str(suite.get("path") or ""),
            testprojectid=str(project.get("id") or ""),
        )
        source = "testlink"
    else:
        if not args.project_contains and not args.suite_contains:
            raise TestLinkError(
                "Use --project plus --suite-id/--suite-name, or search with --project-contains/--suite-contains."
            )
        catalog_file = catalog_path(args.catalog)
        if args.refresh:
            client = parse_common_env(args)
            catalog = build_catalog(
                client,
                project_contains=args.project_contains,
                active_only=args.active_only,
                recursive=args.recursive,
                max_projects=args.max_projects,
            )
            write_catalog(catalog, catalog_file, force=True)
            source = "refreshed-catalog"
        elif catalog_file.exists():
            catalog = read_catalog(catalog_file)
            source = "catalog"
        elif args.offline:
            raise TestLinkError(f"Catalog file does not exist: {catalog_file}. Run refresh-catalog first.")
        else:
            client = parse_common_env(args)
            catalog = build_catalog(
                client,
                project_contains=args.project_contains,
                active_only=args.active_only,
                recursive=args.recursive,
                max_projects=args.max_projects,
            )
            write_catalog(catalog, catalog_file, force=True)
            source = "auto-created-catalog"

        matches, _scanned_projects = find_suites_in_catalog(
            catalog,
            args.project_contains,
            args.suite_contains,
            active_only=args.active_only,
        )
        if len(matches) != 1:
            raise TestLinkError(
                "Profile search must match exactly one suite. "
                f"Matched {len(matches)}. Refine --project-contains/--suite-contains or use --project and --suite-id."
            )
        profile = profile_from_suite_search_row(matches[0])

    saved = save_profile(path, args.name, profile, force=args.force)
    print(
        json.dumps(
            {
                "profile": args.name,
                "profiles_file": str(path),
                "source": source,
                "target": saved,
                "create_example": (
                    f'python .\\testlink_agent.py create-testcase --profile "{args.name}" '
                    f'--name "your_testcase_name" --summary "your summary" '
                    f'--step "Action => Expected result"'
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0

def command_list_profiles(args: argparse.Namespace) -> int:
    path = profiles_path(args.profiles)
    rows = list_profiles(path)
    print(
        json.dumps(
            {
                "profiles_file": str(path),
                "profile_count": len(rows),
                "profiles": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0

def command_delete_profile(args: argparse.Namespace) -> int:
    path = profiles_path(args.profiles)
    removed = delete_profile(path, args.name)
    print(
        json.dumps(
            {
                "profile": args.name,
                "profiles_file": str(path),
                "removed": removed,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0

def command_list_suites(args: argparse.Namespace) -> int:
    client = parse_common_env(args)
    project = client.get_project_by_name(args.project)
    suites = collect_test_suites(
        client,
        str(project["id"]),
        parent_suite_id=args.parent_suite_id,
        recursive=args.recursive,
    )
    rows = [
        {
            "id": suite.get("id"),
            "name": suite.get("name"),
            "parent_id": suite.get("parent_id"),
            "path": suite.get("path"),
            "depth": suite.get("depth"),
            "node_order": suite.get("node_order"),
        }
        for suite in suites
    ]
    print(
        json.dumps(
            {
                "project": project.get("name") or args.project,
                "testprojectid": project.get("id"),
                "recursive": args.recursive,
                "suite_count": len(rows),
                "suites": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0

def command_find_suites(args: argparse.Namespace) -> int:
    if not args.project_contains and not args.suite_contains:
        raise TestLinkError("Use --project-contains, --suite-contains, or both.")

    path = catalog_path(args.catalog)
    source = "catalog"
    failures: list[dict[str, Any]] = []
    catalog: dict[str, Any] | None = None

    if args.refresh:
        client = parse_common_env(args)
        catalog = build_catalog(
            client,
            project_contains=args.project_contains,
            active_only=args.active_only,
            recursive=args.recursive,
            max_projects=args.max_projects,
        )
        write_catalog(catalog, path, force=True)
        source = "refreshed-catalog"
    elif path.exists():
        catalog = read_catalog(path)
    elif args.offline:
        raise TestLinkError(f"Catalog file does not exist: {path}. Run refresh-catalog first.")
    else:
        client = parse_common_env(args)
        catalog = build_catalog(
            client,
            project_contains=args.project_contains,
            active_only=args.active_only,
            recursive=args.recursive,
            max_projects=args.max_projects,
        )
        write_catalog(catalog, path, force=True)
        source = "auto-created-catalog"

    matches, scanned_projects = find_suites_in_catalog(
        catalog,
        args.project_contains,
        args.suite_contains,
        active_only=args.active_only,
    )
    failures = list(catalog.get("scan_failures") or [])

    print(
        json.dumps(
            {
                "source": source,
                "catalog": str(path),
                "catalog_generated_at": catalog.get("generated_at"),
                "project_contains": args.project_contains,
                "suite_contains": args.suite_contains,
                "active_only": args.active_only,
                "recursive": catalog.get("recursive", args.recursive),
                "scanned_project_count": len(scanned_projects),
                "match_count": len(matches),
                "matches": matches,
                "scan_failures": failures,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0

def command_refresh_catalog(args: argparse.Namespace) -> int:
    client = parse_common_env(args)
    path = catalog_path(args.out)
    catalog = build_catalog(
        client,
        project_contains=args.project_contains,
        active_only=args.active_only,
        recursive=args.recursive,
        max_projects=args.max_projects,
    )
    write_catalog(catalog, path, force=args.force)
    print(
        json.dumps(
            {
                "output": str(path),
                "generated_at": catalog.get("generated_at"),
                "project_count": catalog.get("project_count"),
                "scan_failures": catalog.get("scan_failures"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0

def command_list_projects(args: argparse.Namespace) -> int:
    client = parse_common_env(args)
    projects = client.get_projects()
    rows = [
        {
            "id": project.get("id"),
            "name": project.get("name"),
            "prefix": project.get("prefix"),
            "active": project.get("active"),
            "is_public": project.get("is_public"),
        }
        for project in projects or []
    ]
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0

def command_list_plans(args: argparse.Namespace) -> int:
    client = parse_common_env(args)
    project = client.get_project_by_name(args.project)
    plans = client.get_project_test_plans(str(project["id"]))
    rows = [
        {
            "id": plan.get("id"),
            "name": plan.get("name"),
            "active": plan.get("active"),
            "is_public": plan.get("is_public"),
            "testproject_id": plan.get("testproject_id"),
        }
        for plan in plans
    ]
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0

def command_list_platforms(args: argparse.Namespace) -> int:
    client = parse_common_env(args)
    plan = client.get_test_plan_by_name(args.project, args.plan)
    platforms = client.get_platforms(str(plan["id"]))
    rows = [
        {
            "id": platform.get("id"),
            "name": platform.get("name"),
        }
        for platform in platforms
    ]
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0

def command_list_builds(args: argparse.Namespace) -> int:
    client = parse_common_env(args)
    plan = client.get_test_plan_by_name(args.project, args.plan)
    builds = client.get_builds(str(plan["id"]))
    rows = [
        {
            "id": build.get("id"),
            "name": build.get("name"),
            "active": build.get("active"),
            "is_open": build.get("is_open"),
            "release_date": build.get("release_date"),
            "closed_on_date": build.get("closed_on_date"),
            "creation_ts": build.get("creation_ts"),
        }
        for build in builds
    ]
    if args.open_only:
        rows = [row for row in rows if str(row.get("active")) == "1" and str(row.get("is_open")) == "1"]
    rows = sorted(rows, key=lambda row: str(row.get("creation_ts") or ""), reverse=True)
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0
