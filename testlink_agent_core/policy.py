from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from .errors import TestLinkError
from .models import ParsedResult


FORMAL_ENVIRONMENT = "corp"
SANDBOX_ENVIRONMENT = "sandbox"
VALID_ENVIRONMENTS = {FORMAL_ENVIRONMENT, SANDBOX_ENVIRONMENT}
MANAGER_ONLY_REDMINE_FIELDS = ("assigned_to_id", "fixed_version_id")
MULTI_ROW_NOT_AUTHORIZED = "MULTI_ROW_NOT_AUTHORIZED"

_WHITESPACE_RE = re.compile(r"\s+")
_NOISY_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_NOISY_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s]+|/[^\s]+")


def normalize_policy_text(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = _NOISY_HEX_RE.sub("<hex>", text)
    text = _NOISY_PATH_RE.sub("<path>", text)
    return _WHITESPACE_RE.sub(" ", text)


def current_testlink_profile() -> str:
    return normalize_policy_text(os.environ.get("TESTLINK_AGENT_PROFILE") or FORMAL_ENVIRONMENT)


def current_redmine_environment() -> str:
    return normalize_policy_text(os.environ.get("REDMINE_ENV") or FORMAL_ENVIRONMENT)


def validate_environment_pair(
    testlink_profile: str | None = None,
    redmine_environment: str | None = None,
) -> tuple[str, str]:
    profile = normalize_policy_text(testlink_profile or current_testlink_profile())
    redmine_env = normalize_policy_text(redmine_environment or current_redmine_environment())
    if profile not in VALID_ENVIRONMENTS:
        raise TestLinkError(f"Unsupported TESTLINK_AGENT_PROFILE: {profile}")
    if redmine_env not in VALID_ENVIRONMENTS:
        raise TestLinkError(f"Unsupported REDMINE_ENV: {redmine_env}")
    if profile != redmine_env:
        raise TestLinkError(
            "TESTLINK_AGENT_PROFILE and REDMINE_ENV must match before write operations: "
            f"{profile} != {redmine_env}"
        )
    return profile, redmine_env


def build_failure_signature(result: ParsedResult, failure_summary: str | None = None) -> str:
    summary = normalize_policy_text(failure_summary)
    parts = [
        normalize_policy_text(result.test_name),
        normalize_policy_text(result.raw_status),
    ]
    if summary:
        parts.append(summary)
    return "|".join(part for part in parts if part)


def build_dedupe_key(
    *,
    redmine_project_id: str,
    context: dict[str, Any],
    result: ParsedResult,
    failure_summary: str | None = None,
) -> str:
    plan = context.get("plan") or {}
    project = context.get("project") or {}
    platform = context.get("platform") or {}
    parts = [
        ("redmine_project_id", redmine_project_id),
        ("testlink_project", project.get("name") or project.get("id") or ""),
        ("testlink_plan", plan.get("name") or plan.get("id") or ""),
        ("platform", platform.get("name") or platform.get("id") or ""),
        ("testcase_external_id", result.external_id),
        ("failure_signature", build_failure_signature(result, failure_summary=failure_summary)),
    ]
    return "\n".join(f"{name}={normalize_policy_text(value)}" for name, value in parts)


def dedupe_digest(dedupe_key: str, *, length: int = 16) -> str:
    if length < 8:
        raise ValueError("Dedupe digest length must be at least 8.")
    return hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:length]


def dedupe_marker(digest: str) -> str:
    return f"testlink-agent:{digest}"


def redmine_manager_fields_allowed() -> bool:
    return normalize_policy_text(os.environ.get("REDMINE_ALLOW_MANAGER_FIELDS")) in {"1", "true", "yes", "on"}


def blocked_manager_fields(fields: dict[str, Any]) -> list[str]:
    if redmine_manager_fields_allowed():
        return []
    return [field for field in MANAGER_ONLY_REDMINE_FIELDS if str(fields.get(field) or "").strip()]


def validate_testcase_row_policy(
    *,
    single_step: bool = True,
    allow_multi_row: bool = False,
) -> str:
    """Fail closed unless multi-row output is explicitly and consistently authorized."""

    if single_step and allow_multi_row:
        raise TestLinkError(
            "allow_multi_row=true requires single_step=false; refusing an ambiguous testcase row policy.",
            code=MULTI_ROW_NOT_AUTHORIZED,
        )
    if not single_step and not allow_multi_row:
        raise TestLinkError(
            "single_step=false requires allow_multi_row=true; multi-row testcase writes are not authorized.",
            code=MULTI_ROW_NOT_AUTHORIZED,
        )
    return "single-row" if single_step else "multi-row-authorized"
