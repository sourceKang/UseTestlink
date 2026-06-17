from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import STATUS_TO_TESTLINK
from .models import ParsedResult


REPORT_LINE_RE = re.compile(
    r"^\[(?P<external_id>[A-Za-z0-9]+-\d+)\]\[(?P<test_name>.*)\]\s+"
    r"Result\s+(?P<result>Pass|Fail|Blocked|Skip|Skipped|Error)\s+"
    r"\((?P<duration>[^)]*)\)",
    re.IGNORECASE,
)


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

def choose_latest_open_build(builds: list[dict[str, Any]]) -> dict[str, Any] | None:
    open_builds = [
        build
        for build in builds
        if str(build.get("active")) == "1" and str(build.get("is_open")) == "1"
    ]
    if not open_builds:
        return None
    return sorted(
        open_builds,
        key=lambda build: (
            str(build.get("creation_ts") or ""),
            str(build.get("release_date") or ""),
            str(build.get("id") or ""),
        ),
        reverse=True,
    )[0]

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
