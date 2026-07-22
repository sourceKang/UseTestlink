from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path
from typing import Any

from .config import EXECUTION_TYPE_TO_TESTLINK, IMPORTANCE_TO_TESTLINK
from .errors import TestLinkError
from .policy import validate_testcase_row_policy


_BR_RE = re.compile(r"<br\s*/?>[ \t]*(?:\r?\n)?", flags=re.IGNORECASE)
_NUMBERED_LINE_RE = re.compile(r"^\s*(\d+)\.\s*(.+?)\s*$")


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

def normalize_testcase(case: dict[str, Any]) -> dict[str, Any]:
    external_id = str(
        case.get("full_external_id")
        or case.get("external_id")
        or case.get("tc_external_id")
        or ""
    )
    testcase_id = str(case.get("tcase_id") or case.get("tc_id") or case.get("testcase_id") or case.get("id") or "")
    return {
        "external_id": external_id,
        "testcase_id": testcase_id,
        "version": case.get("version"),
        "name": case.get("tcase_name") or case.get("name"),
        "execution_order": case.get("execution_order"),
        "platform_id": case.get("platform_id"),
        "raw": case,
    }

def read_optional_text(text: str | None, file_path: str | None, label: str) -> str:
    if text and file_path:
        raise TestLinkError(f"Use either --{label} or --{label}-file, not both.")
    if file_path:
        path = Path(file_path)
        if not path.exists():
            raise TestLinkError(f"{label} file does not exist: {path}")
        return path.read_text(encoding="utf-8")
    return text or ""

def preserve_testlink_line_breaks(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.rstrip("\n")
    if "\n" not in normalized:
        return normalized
    return "<br />\n".join(normalized.split("\n"))


def canonicalize_testlink_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = _BR_RE.sub("\n", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _numbered_items(value: Any) -> list[tuple[int, str]] | None:
    lines = [line for line in canonicalize_testlink_text(value).split("\n") if line.strip()]
    matches = [_NUMBERED_LINE_RE.fullmatch(line) for line in lines]
    if not any(matches):
        return None
    if not all(matches):
        raise TestLinkError("Numbered testcase content must number every non-empty line.")
    return [(int(match.group(1)), match.group(2).strip()) for match in matches if match is not None]


def validate_testcase_steps(
    steps: Any,
    *,
    single_step: bool = True,
    allow_multi_row: bool = False,
) -> dict[str, Any]:
    row_policy = validate_testcase_row_policy(
        single_step=single_step,
        allow_multi_row=allow_multi_row,
    )
    if not isinstance(steps, list) or not steps:
        raise TestLinkError("At least one testcase step row is required.")
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise TestLinkError(f"Testcase step row {index} must be an object.")
        if not canonicalize_testlink_text(step.get("actions") or step.get("action")):
            raise TestLinkError(f"Testcase step row {index} is missing actions.")
        if not canonicalize_testlink_text(
            step.get("expected_results") or step.get("expected") or step.get("result")
        ):
            raise TestLinkError(f"Testcase step row {index} is missing expected results.")

    logical_step_count = len(steps)
    if single_step:
        if len(steps) != 1:
            raise TestLinkError("single_step=true requires exactly one TestLink step row.")
        actions = _numbered_items(steps[0].get("actions"))
        expected = _numbered_items(steps[0].get("expected_results"))
        if (actions is None) != (expected is None):
            raise TestLinkError("Actions and Expected must use matching numbering.")
        if actions is not None and expected is not None:
            action_numbers = [number for number, _ in actions]
            expected_numbers = [number for number, _ in expected]
            required_numbers = list(range(1, len(actions) + 1))
            if action_numbers != expected_numbers or action_numbers != required_numbers:
                raise TestLinkError("Actions and Expected numbering must match and be contiguous from 1.")
            logical_step_count = len(actions)
        else:
            logical_step_count = 1

    return {
        "row_policy": row_policy,
        "logical_step_count": logical_step_count,
        "planned_row_count": len(steps),
        "allow_multi_row": allow_multi_row,
    }


def _first_testcase_record(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                try:
                    return _first_testcase_record(item)
                except TestLinkError:
                    continue
    if isinstance(value, dict):
        record_keys = {
            "testcaseid",
            "testcase_id",
            "tcase_id",
            "testcasename",
            "tcase_name",
            "name",
            "summary",
            "steps",
        }
        if record_keys.intersection(value):
            return value
        for child in value.values():
            if isinstance(child, (dict, list)):
                try:
                    return _first_testcase_record(child)
                except TestLinkError:
                    continue
    raise TestLinkError("TestLink testcase readback did not contain a testcase record.")


def _step_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if {"actions", "action", "expected_results", "expected"}.intersection(value):
            return [value]
        rows: list[dict[str, Any]] = []
        for child in value.values():
            rows.extend(_step_rows(child))
        return rows
    return []


def canonical_testcase_content(value: Any, *, expected_payload: dict[str, Any]) -> dict[str, Any]:
    record = _first_testcase_record(value) if value is not expected_payload else value
    canonical: dict[str, Any] = {}
    field_aliases = {
        "testcasename": ("testcasename", "tcase_name", "name"),
        "summary": ("summary",),
        "preconditions": ("preconditions",),
        "importance": ("importance",),
        "executiontype": ("executiontype", "execution_type"),
    }
    for expected_key, aliases in field_aliases.items():
        if expected_key not in expected_payload:
            continue
        raw = next((record.get(alias) for alias in aliases if alias in record), None)
        if expected_key in {"summary", "preconditions", "testcasename"}:
            canonical[expected_key] = canonicalize_testlink_text(raw)
        else:
            canonical[expected_key] = str(raw or "").strip()

    if "steps" in expected_payload:
        rows = _step_rows(record.get("steps"))
        canonical["steps"] = [
            {
                "actions": canonicalize_testlink_text(row.get("actions") or row.get("action")),
                "expected_results": canonicalize_testlink_text(
                    row.get("expected_results") or row.get("expected") or row.get("result")
                ),
                "execution_type": str(
                    row.get("execution_type") or row.get("executiontype") or ""
                ).strip(),
            }
            for row in rows
        ]
    return canonical


def compare_testcase_readback(
    expected_payload: dict[str, Any],
    readback: Any,
) -> dict[str, Any]:
    expected = canonical_testcase_content(expected_payload, expected_payload=expected_payload)
    actual = canonical_testcase_content(readback, expected_payload=expected_payload)
    mismatches = [key for key in expected if expected.get(key) != actual.get(key)]
    return {
        "matches": not mismatches,
        "mismatches": mismatches,
        "expected": expected,
        "actual": actual,
    }

def coerce_testlink_enum(value: str | int, mapping: dict[str, int], label: str) -> int:
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    mapped = mapping.get(text.casefold())
    if mapped is None:
        allowed = ", ".join(sorted(mapping))
        raise TestLinkError(f"Unsupported {label}: {value}. Use one of: {allowed}, or a numeric TestLink value.")
    return mapped

def parse_step_text(value: str) -> dict[str, Any]:
    if "=>" in value:
        actions, expected = value.split("=>", 1)
    else:
        actions, expected = value, ""
    return {
        "actions": actions.strip(),
        "expected_results": expected.strip(),
    }

def normalize_create_step(step: dict[str, Any], step_number: int, default_execution_type: int) -> dict[str, Any]:
    actions = str(step.get("actions") or step.get("action") or "").strip()
    expected = str(
        step.get("expected_results")
        or step.get("expected")
        or step.get("result")
        or ""
    ).strip()
    if not actions:
        raise TestLinkError(f"Step {step_number} is missing actions.")
    if not expected:
        raise TestLinkError(f"Step {step_number} is missing expected results.")
    execution_type = step.get("execution_type") or step.get("executiontype") or default_execution_type
    return {
        "step_number": step_number,
        "actions": preserve_testlink_line_breaks(actions),
        "expected_results": preserve_testlink_line_breaks(expected),
        "execution_type": coerce_testlink_enum(execution_type, EXECUTION_TYPE_TO_TESTLINK, "step execution type"),
    }

def collapse_raw_steps(raw_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(raw_steps) <= 1:
        return raw_steps

    actions_lines: list[str] = []
    expected_lines: list[str] = []
    for index, step in enumerate(raw_steps, start=1):
        actions = str(step.get("actions") or step.get("action") or "").strip()
        expected = str(
            step.get("expected_results")
            or step.get("expected")
            or step.get("result")
            or ""
        ).strip()
        if not actions:
            raise TestLinkError(f"Step {index} is missing actions.")
        actions_lines.append(f"{index}. {actions}")
        expected_lines.append(f"{index}. {expected}" if expected else f"{index}.")

    execution_type = raw_steps[0].get("execution_type") or raw_steps[0].get("executiontype")
    collapsed: dict[str, Any] = {
        "actions": "\n".join(actions_lines),
        "expected_results": "\n".join(expected_lines),
    }
    if execution_type not in (None, ""):
        collapsed["execution_type"] = execution_type
    return [collapsed]

def parse_create_steps(args: argparse.Namespace) -> list[dict[str, Any]]:
    raw_steps: list[dict[str, Any]] = []
    if args.steps_file:
        steps_path = Path(args.steps_file)
        if not steps_path.exists():
            raise TestLinkError(f"Steps file does not exist: {steps_path}")
        try:
            parsed = json.loads(steps_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TestLinkError(f"Steps file must be JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise TestLinkError("Steps file must contain a JSON array.")
        for index, item in enumerate(parsed, start=1):
            if isinstance(item, str):
                raw_steps.append(parse_step_text(item))
            elif isinstance(item, dict):
                raw_steps.append(item)
            else:
                raise TestLinkError(f"Step {index} must be a string or object.")

    for step in args.step or []:
        raw_steps.append(parse_step_text(step))

    if not raw_steps:
        raise TestLinkError("At least one --step or --steps-file entry is required.")

    single_step = bool(getattr(args, "single_step", True))
    allow_multi_row = bool(getattr(args, "allow_multi_row", False))
    validate_testcase_row_policy(single_step=single_step, allow_multi_row=allow_multi_row)
    if single_step:
        raw_steps = collapse_raw_steps(raw_steps)

    default_execution_type = coerce_testlink_enum(
        getattr(args, "execution_type", None) or "manual",
        EXECUTION_TYPE_TO_TESTLINK,
        "execution type",
    )
    normalized = [
        normalize_create_step(step, index, default_execution_type)
        for index, step in enumerate(raw_steps, start=1)
    ]
    validate_testcase_steps(
        normalized,
        single_step=single_step,
        allow_multi_row=allow_multi_row,
    )
    return normalized

def parse_optional_steps(args: argparse.Namespace) -> list[dict[str, Any]] | None:
    if not getattr(args, "step", None) and not getattr(args, "steps_file", None):
        return None
    return parse_create_steps(args)

def create_testcase_payload(
    args: argparse.Namespace,
    project: dict[str, Any],
    suite_id: str | None = None,
) -> dict[str, Any]:
    author_login = str(args.author_login or os.environ.get("TESTLINK_AUTHOR_LOGIN", "")).strip()
    if not author_login:
        raise TestLinkError("--author-login or TESTLINK_AUTHOR_LOGIN is required.")

    resolved_suite_id = suite_id or str(getattr(args, "suite_id", "") or "").strip()
    if not resolved_suite_id:
        raise TestLinkError("--suite-id or --suite-name is required.")

    payload: dict[str, Any] = {
        "testprojectid": str(project["id"]),
        "testsuiteid": resolved_suite_id,
        "testcasename": args.name,
        "authorlogin": author_login,
        "summary": preserve_testlink_line_breaks(read_optional_text(args.summary, args.summary_file, "summary")),
        "steps": parse_create_steps(args),
        "importance": coerce_testlink_enum(args.importance, IMPORTANCE_TO_TESTLINK, "importance"),
        "executiontype": coerce_testlink_enum(args.execution_type, EXECUTION_TYPE_TO_TESTLINK, "execution type"),
        "checkduplicatedname": 1,
        "actiononduplicatedname": args.duplicate_action.replace("-", "_"),
    }
    preconditions = read_optional_text(args.preconditions, args.preconditions_file, "preconditions")
    if preconditions:
        payload["preconditions"] = preserve_testlink_line_breaks(preconditions)
    if args.order is not None:
        payload["order"] = args.order
    return payload

def update_testcase_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    testcase_id = str(getattr(args, "testcase_id", "") or "").strip()
    testcase_external_id = str(getattr(args, "testcase_external_id", "") or "").strip()
    if testcase_id:
        payload["testcaseid"] = testcase_id
    elif testcase_external_id:
        payload["testcaseexternalid"] = testcase_external_id
    else:
        raise TestLinkError("--testcase-id or --testcase-external-id is required.")

    if getattr(args, "version", None):
        payload["version"] = int(args.version) if str(args.version).isdigit() else args.version
    if getattr(args, "name", None):
        payload["testcasename"] = args.name
    if getattr(args, "summary", None) is not None or getattr(args, "summary_file", None):
        payload["summary"] = preserve_testlink_line_breaks(read_optional_text(args.summary, args.summary_file, "summary"))
    if getattr(args, "preconditions", None) is not None or getattr(args, "preconditions_file", None):
        payload["preconditions"] = preserve_testlink_line_breaks(
            read_optional_text(args.preconditions, args.preconditions_file, "preconditions")
        )

    steps = parse_optional_steps(args)
    if steps is not None:
        payload["steps"] = steps
    if getattr(args, "importance", None):
        payload["importance"] = coerce_testlink_enum(args.importance, IMPORTANCE_TO_TESTLINK, "importance")
    if getattr(args, "execution_type", None):
        payload["executiontype"] = coerce_testlink_enum(
            args.execution_type,
            EXECUTION_TYPE_TO_TESTLINK,
            "execution type",
        )

    update_fields = set(payload) - {"testcaseid", "testcaseexternalid", "version"}
    if not update_fields:
        raise TestLinkError(
            "Nothing to update. Add --name, --summary, --preconditions, --step, "
            "--steps-file, --importance, or --execution-type."
        )
    return payload
