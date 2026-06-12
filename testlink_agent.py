#!/usr/bin/env python3
"""Small TestLink XML-RPC helper for report preview and result upload."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import xmlrpc.client
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_TIMEOUT_SECONDS = 60
REPORT_LINE_RE = re.compile(
    r"^\[(?P<external_id>[A-Za-z0-9]+-\d+)\]\[(?P<test_name>.*)\]\s+"
    r"Result\s+(?P<result>Pass|Fail|Blocked|Skip|Skipped|Error)\s+"
    r"\((?P<duration>[^)]*)\)",
    re.IGNORECASE,
)

STATUS_TO_TESTLINK = {
    "pass": "p",
    "fail": "f",
    "blocked": "b",
    "error": "f",
}


@dataclass
class ParsedResult:
    external_id: str
    test_name: str
    raw_status: str
    status: str | None
    duration_text: str
    duration_seconds: float | None
    testcase_id: str | None = None
    version: str | None = None
    testlink_name: str | None = None


class TestLinkError(RuntimeError):
    pass


class TestLinkClient:
    def __init__(self, base_url: str, devkey: str, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.endpoint = normalize_endpoint(base_url)
        self.devkey = devkey
        self.timeout = timeout

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        body = xmlrpc.client.dumps(
            () if params is None else (params,),
            methodname=method,
            allow_none=True,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "text/xml"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read().lstrip()
        values, _method = xmlrpc.client.loads(payload)
        return values[0] if values else None

    def check_devkey(self) -> bool:
        return self.call("tl.checkDevKey", {"devKey": self.devkey}) is True

    def get_project_by_name(self, name: str) -> dict[str, Any]:
        project = self.call(
            "tl.getTestProjectByName",
            {"devKey": self.devkey, "testprojectname": name},
        )
        if not isinstance(project, dict) or "id" not in project:
            raise TestLinkError(f"Project not found or unexpected response: {name}")
        return project

    def get_test_plan_by_name(self, project_name: str, plan_name: str) -> dict[str, Any]:
        plans = self.call(
            "tl.getTestPlanByName",
            {
                "devKey": self.devkey,
                "testprojectname": project_name,
                "testplanname": plan_name,
            },
        )
        if isinstance(plans, list) and plans:
            return plans[0]
        if isinstance(plans, dict) and "id" in plans:
            return plans
        raise TestLinkError(f"Test Plan not found: {project_name} / {plan_name}")

    def get_platform_by_name(self, testplan_id: str, platform_name: str) -> dict[str, Any]:
        platforms = self.call(
            "tl.getTestPlanPlatforms",
            {"devKey": self.devkey, "testplanid": testplan_id},
        )
        for platform in platforms or []:
            if str(platform.get("name", "")).casefold() == platform_name.casefold():
                return platform
        available = ", ".join(str(p.get("name")) for p in platforms or [])
        raise TestLinkError(f"Platform not found: {platform_name}. Available: {available}")

    def get_build(self, testplan_id: str, build: str | None, build_id: str | None) -> dict[str, Any]:
        builds = self.call(
            "tl.getBuildsForTestPlan",
            {"devKey": self.devkey, "testplanid": testplan_id},
        )
        for candidate in builds or []:
            if build_id and str(candidate.get("id")) == str(build_id):
                return candidate
            if build and str(candidate.get("name")) == build:
                return candidate
        target = f"id={build_id}" if build_id else f"name={build}"
        raise TestLinkError(f"Build not found: {target}")

    def get_plan_cases_by_external_id(self, testplan_id: str, platform_id: str) -> dict[str, dict[str, Any]]:
        raw_cases = self.call(
            "tl.getTestCasesForTestPlan",
            {
                "devKey": self.devkey,
                "testplanid": testplan_id,
                "platformid": platform_id,
                "details": "simple",
            },
        )
        cases: dict[str, dict[str, Any]] = {}
        for case in flatten_plan_cases(raw_cases):
            external_id = str(
                case.get("full_external_id")
                or case.get("external_id")
                or case.get("tc_external_id")
                or ""
            )
            if external_id and external_id != "None":
                cases[external_id] = case
        return cases

    def report_result(self, params: dict[str, Any]) -> Any:
        payload = {"devKey": self.devkey, **params}
        return self.call("tl.reportTCResult", payload)


def normalize_endpoint(url: str) -> str:
    if not url:
        raise TestLinkError("TESTLINK_URL is required.")

    parsed = urlsplit(url.strip())
    path = parsed.path.rstrip("/")

    if path.endswith("/lib/api/xmlrpc/v1/xmlrpc.php"):
        endpoint_path = path
    elif "/index.php" in path:
        endpoint_path = path.split("/index.php", 1)[0].rstrip("/") + "/lib/api/xmlrpc/v1/xmlrpc.php"
    else:
        endpoint_path = path + "/lib/api/xmlrpc/v1/xmlrpc.php"

    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, "", ""))


def parse_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        raise TestLinkError(f"Env file does not exist: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def parse_duration_seconds(value: str) -> float | None:
    duration = value.strip().lower()
    if duration.endswith("s"):
        duration = duration[:-1].strip()
    try:
        return float(duration)
    except ValueError:
        return None


def parse_report(path: Path) -> tuple[dict[str, str], list[ParsedResult]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    header: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            if key in {
                "Report generated on",
                "Total test time",
                "Summary",
                "UI URL",
                "EMS Version",
                "Node Name",
                "Node IP",
                "Node Chassis",
                "Test Target Source",
            }:
                header[key] = value.strip()
        if line.strip() == "Test Results:":
            break

    results: list[ParsedResult] = []
    for line in text.splitlines():
        match = REPORT_LINE_RE.match(line.strip())
        if not match:
            continue
        raw_status = match.group("result")
        status = STATUS_TO_TESTLINK.get(raw_status.lower())
        duration_text = match.group("duration")
        results.append(
            ParsedResult(
                external_id=match.group("external_id"),
                test_name=match.group("test_name"),
                raw_status=raw_status,
                status=status,
                duration_text=duration_text,
                duration_seconds=parse_duration_seconds(duration_text),
            )
        )
    return header, results


def flatten_plan_cases(raw: Any) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if (
                any(key in value for key in ("tcase_id", "tc_id", "testcase_id", "external_id", "full_external_id"))
                and ("name" in value or "tcase_name" in value)
            ):
                cases.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(raw)
    return cases


def map_results_to_plan(results: list[ParsedResult], plan_cases: dict[str, dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for result in results:
        case = plan_cases.get(result.external_id)
        if not case:
            missing.append(result.external_id)
            continue
        result.testcase_id = str(case.get("tcase_id") or case.get("tc_id") or case.get("testcase_id") or case.get("id") or "")
        result.version = str(case.get("version") or "")
        result.testlink_name = case.get("tcase_name") or case.get("name")
    return missing


def build_notes(header: dict[str, str], report_path: Path, result: ParsedResult) -> str:
    generated = header.get("Report generated on", "")
    ems_version = header.get("EMS Version", "")
    node_name = header.get("Node Name", "")
    node_ip = header.get("Node IP", "")

    lines = [
        "Automation Source: Web EMS Rest API automation report",
        f"Report generated time: {generated}",
        f"EMS Version: {ems_version}",
        f"Node: {node_name} / {node_ip}",
        f"Test function: {result.test_name}",
        f"Original result: Result {result.raw_status}",
        f"Duration: {result.duration_text}",
        f"Report file: {report_path.name}",
    ]
    if result.status == "f":
        lines.append("Failure summary: Result Fail")
    return "\n".join(lines)


def result_to_dict(result: ParsedResult) -> dict[str, Any]:
    return {
        "external_id": result.external_id,
        "testcase_id": result.testcase_id,
        "version": result.version,
        "test_name": result.test_name,
        "testlink_name": result.testlink_name,
        "raw_status": result.raw_status,
        "status": result.status,
        "duration": result.duration_text,
    }


def resolve_context(args: argparse.Namespace, client: TestLinkClient) -> dict[str, Any]:
    if not client.check_devkey():
        raise TestLinkError("tl.checkDevKey failed.")

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
    parse_env_file(args.env_file)
    base_url = args.url or os.environ.get("TESTLINK_URL", "")
    devkey = args.devkey or os.environ.get("TESTLINK_DEVKEY", "")
    if not devkey:
        raise TestLinkError("TESTLINK_DEVKEY is required.")

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

    client = TestLinkClient(base_url, devkey, timeout=args.timeout)
    context = resolve_context(args, client)

    plan_id = str(context["plan"]["id"])
    platform_id = str(context["platform"]["id"])
    build_id = str(context["build"]["id"])
    plan_cases = client.get_plan_cases_by_external_id(plan_id, platform_id)
    missing = map_results_to_plan(writable, plan_cases)
    if missing:
        raise TestLinkError(f"Test cases not found in plan/platform: {', '.join(sorted(missing))}")

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
    }

    if not args.write:
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        return 0

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, result in enumerate(writable, start=1):
        params: dict[str, Any] = {
            "testcaseexternalid": result.external_id,
            "testplanid": plan_id,
            "buildid": build_id,
            "platformid": platform_id,
            "platformname": context["platform"].get("name"),
            "status": result.status,
            "notes": build_notes(header, report_path, result),
        }
        if result.duration_seconds is not None:
            params["execduration"] = round(result.duration_seconds / 60.0, 4)

        try:
            response = client.report_result(params)
            successes.append({"external_id": result.external_id, "status": result.status, "response": response})
        except xmlrpc.client.Fault as fault:
            failures.append(
                {
                    "external_id": result.external_id,
                    "status": result.status,
                    "fault_code": fault.faultCode,
                    "fault_message": fault.faultString,
                }
            )
        except Exception as exc:
            failures.append({"external_id": result.external_id, "status": result.status, "error": str(exc)})

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


def command_list_projects(args: argparse.Namespace) -> int:
    parse_env_file(args.env_file)
    base_url = args.url or os.environ.get("TESTLINK_URL", "")
    devkey = args.devkey or os.environ.get("TESTLINK_DEVKEY", "")
    if not devkey:
        raise TestLinkError("TESTLINK_DEVKEY is required.")

    client = TestLinkClient(base_url, devkey, timeout=args.timeout)
    if not client.check_devkey():
        raise TestLinkError("tl.checkDevKey failed.")
    projects = client.call("tl.getProjects", {"devKey": client.devkey})
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TestLink XML-RPC helper.")
    parser.add_argument("--url", help="TestLink base URL or XML-RPC endpoint. Defaults to TESTLINK_URL.")
    parser.add_argument("--devkey", help="Personal API access key. Prefer TESTLINK_DEVKEY.")
    parser.add_argument("--env-file", help="Optional .env file to load before reading environment variables.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_projects = subparsers.add_parser("list-projects", help="List visible TestLink projects.")
    list_projects.set_defaults(func=command_list_projects)

    upload = subparsers.add_parser("upload-report", help="Preview or upload an automation report.")
    upload.add_argument("--project", required=True)
    upload.add_argument("--plan", required=True)
    upload.add_argument("--platform", required=True)
    build_group = upload.add_mutually_exclusive_group(required=True)
    build_group.add_argument("--build", help="Build name.")
    build_group.add_argument("--build-id", help="Build ID.")
    upload.add_argument("--report", required=True)
    upload.add_argument("--skip-policy", choices=("ignore", "blocked"), default="ignore")
    upload.add_argument("--write", action="store_true", help="Actually write results. Omit for preview only.")
    upload.add_argument("--require-open-build", action="store_true", default=True)
    upload.add_argument("--progress", type=int, default=25)
    upload.add_argument("--throttle", type=float, default=0.03)
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


if __name__ == "__main__":
    raise SystemExit(main())
