from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .config import STATUS_TO_TESTLINK
from .errors import TestLinkError
from .models import ParsedResult


LEGACY_WEB_EMS_SCHEMA = "legacy-web-ems-report-v1"
JUNIT_XML_SCHEMA = "junit-xml-v1"
SCHEMA_HEADER_KEY = "_schema_version"
TEST_RESULTS_MARKER = "Test Results:"

# A TestLink external ID written out in full, for example EMS1-3581.
EXTERNAL_ID_LITERAL_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]*-\d+)\b")
# The same ID as a pytest function name carries it, for example
# test_ems1_3581_all_port_info. The digits must follow the prefix directly, so
# names such as test_create_submap_and_node do not produce an ID.
# The trailing lookahead rather than \b, because a word boundary never matches
# between the digits and the underscore that follows them in a pytest name.
EXTERNAL_ID_IN_NAME_RE = re.compile(r"\btest[_-]([A-Za-z][A-Za-z0-9]*)[_-](\d+)(?!\d)")

REPORT_LINE_RE = re.compile(
    r"^\[(?P<external_id>[A-Za-z0-9]+-\d+)\]\[(?P<test_name>.*)\]\s+"
    r"Result\s+(?P<result>Pass|Fail|Blocked|Skip|Skipped|Error)\s+"
    r"\((?P<duration>[^)]*)\)",
    re.IGNORECASE,
)


def read_report_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TestLinkError(f"Report file must be UTF-8 encoded: {path}: {exc}") from exc


def looks_like_junit_xml(text: str) -> bool:
    head = text.lstrip()
    return head.startswith("<") and ("<testsuite" in head[:4096] or "<testsuites" in head[:4096])


def junit_external_id(test_name: str, classname: str) -> str | None:
    """Recover the TestLink external ID a pytest JUnit entry carries.

    JUnit XML has no field for it, so automation encodes it in the test function
    name. Both the written-out form (EMS1-3581) and the pytest form
    (test_ems1_3581_all_port_info) are accepted, name before classname.
    """
    for source in (test_name or "", classname or ""):
        literal = EXTERNAL_ID_LITERAL_RE.search(source)
        if literal:
            return literal.group(1).upper()
        embedded = EXTERNAL_ID_IN_NAME_RE.search(source)
        if embedded:
            return f"{embedded.group(1).upper()}-{embedded.group(2)}"
    return None


def junit_case_status(case: ET.Element) -> str:
    for child in case:
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in ("failure", "error"):
            return "Error" if tag == "error" else "Fail"
        if tag == "skipped":
            return "Skipped"
    return "Pass"


def parse_junit_report(text: str) -> tuple[dict[str, str], list[ParsedResult]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise TestLinkError(f"Unsupported report schema: JUnit XML is not well formed: {exc}") from exc

    suites = [root] if root.tag.rsplit("}", 1)[-1] == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        raise TestLinkError("Unsupported report schema: no testsuite element was found.")

    results: list[ParsedResult] = []
    unmapped = 0
    total_seconds = 0.0
    timestamp = ""
    counts = {"Pass": 0, "Fail": 0, "Error": 0, "Skipped": 0}
    for suite in suites:
        timestamp = suite.get("timestamp") or timestamp
        for case in suite.iter("testcase"):
            raw_status = junit_case_status(case)
            counts[raw_status] += 1
            try:
                duration_seconds: float | None = float(case.get("time") or 0.0)
            except ValueError:
                duration_seconds = None
            total_seconds += duration_seconds or 0.0
            external_id = junit_external_id(case.get("name", ""), case.get("classname", ""))
            if external_id is None:
                unmapped += 1
                continue
            results.append(
                ParsedResult(
                    external_id=external_id,
                    test_name=case.get("name", ""),
                    raw_status=raw_status,
                    status=STATUS_TO_TESTLINK.get(raw_status.lower()),
                    duration_text=f"{duration_seconds:.3f}s" if duration_seconds is not None else "",
                    duration_seconds=duration_seconds,
                )
            )

    if not results:
        raise TestLinkError(
            "Unsupported report schema: no testcase carried a TestLink external ID. "
            "Name the automation function after the case, for example test_ems1_3581_all_port_info."
        )

    failed = counts["Fail"] + counts["Error"]
    header = {
        SCHEMA_HEADER_KEY: JUNIT_XML_SCHEMA,
        "Summary": f"{counts['Pass']} Pass / {failed} Fail / {counts['Skipped']} Skipped",
        "Total test time": f"{total_seconds:.2f} seconds",
    }
    if timestamp:
        header["Report generated on"] = timestamp
    if unmapped:
        header["Unmapped Tests"] = str(unmapped)
    return header, results


def detect_report_schema(text: str) -> str:
    if looks_like_junit_xml(text):
        return JUNIT_XML_SCHEMA
    lines = text.splitlines()
    if TEST_RESULTS_MARKER not in [line.strip() for line in lines]:
        raise TestLinkError(f"Unsupported report schema: missing '{TEST_RESULTS_MARKER}' marker.")
    if not any(REPORT_LINE_RE.match(line.strip()) for line in lines):
        raise TestLinkError("Unsupported report schema: no TestLink result rows were found.")
    return LEGACY_WEB_EMS_SCHEMA


def parse_duration_seconds(value: str) -> float | None:
    duration = value.strip().lower()
    if duration.endswith("s"):
        duration = duration[:-1].strip()
    try:
        return float(duration)
    except ValueError:
        return None

def parse_report(path: Path) -> tuple[dict[str, str], list[ParsedResult]]:
    text = read_report_text(path)
    schema_version = detect_report_schema(text)
    if schema_version == JUNIT_XML_SCHEMA:
        return parse_junit_report(text)
    header: dict[str, str] = {SCHEMA_HEADER_KEY: schema_version}
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
        if line.strip() == TEST_RESULTS_MARKER:
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
