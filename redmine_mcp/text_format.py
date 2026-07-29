from __future__ import annotations

import re
from typing import Any

from .errors import RedmineMcpError, redact_secrets


TEXT_FORMAT_POLICY_VERSION = 1
SUPPORTED_ENGINES = {"markdown", "textile"}
SUPPORTED_VALIDATION_MODES = {"strict", "warn", "off"}
_CONTRACT_KEYS = {"engine", "validation", "policy_version"}
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_TEXTILE_HEADING_RE = re.compile(r"^\s*h([1-6])\.\s+\S", re.IGNORECASE)
_HASH_LINE_RE = re.compile(r"^\s*#\s+\S")
_MAX_EXCERPT_LENGTH = 160


def normalize_text_format_contract(value: Any) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise RedmineMcpError("Template text_format must be an object.", code="TEMPLATE_INVALID")
    unknown = sorted(set(value) - _CONTRACT_KEYS)
    if unknown:
        raise RedmineMcpError(
            "Template text_format contains unsupported fields: " + ", ".join(unknown),
            code="TEMPLATE_INVALID",
        )
    engine = str(value.get("engine") or "").strip().casefold()
    if engine not in SUPPORTED_ENGINES:
        choices = ", ".join(sorted(SUPPORTED_ENGINES))
        raise RedmineMcpError(
            f"Template text_format.engine must be one of: {choices}.",
            code="TEMPLATE_INVALID",
        )
    validation = str(value.get("validation") or "").strip().casefold()
    if validation not in SUPPORTED_VALIDATION_MODES:
        choices = ", ".join(sorted(SUPPORTED_VALIDATION_MODES))
        raise RedmineMcpError(
            f"Template text_format.validation must be one of: {choices}.",
            code="TEMPLATE_INVALID",
        )
    raw_policy_version = value.get("policy_version", TEXT_FORMAT_POLICY_VERSION)
    if isinstance(raw_policy_version, bool):
        raise RedmineMcpError(
            "Template text_format.policy_version must be an integer.",
            code="TEMPLATE_INVALID",
        )
    try:
        policy_version = int(raw_policy_version)
    except (TypeError, ValueError) as exc:
        raise RedmineMcpError(
            "Template text_format.policy_version must be an integer.",
            code="TEMPLATE_INVALID",
        ) from exc
    if policy_version != TEXT_FORMAT_POLICY_VERSION:
        raise RedmineMcpError(
            f"Template text_format.policy_version must be {TEXT_FORMAT_POLICY_VERSION}.",
            code="TEMPLATE_INVALID",
        )
    return {
        "engine": engine,
        "validation": validation,
        "policy_version": policy_version,
    }


def _excerpt(text: str) -> str:
    safe = str(redact_secrets(text.strip()))
    if len(safe) <= _MAX_EXCERPT_LENGTH:
        return safe
    return safe[: _MAX_EXCERPT_LENGTH - 1] + "…"


def _finding(*, line: int, code: str, message: str, text: str) -> dict[str, Any]:
    return {
        "line": line,
        "code": code,
        "message": message,
        "excerpt": _excerpt(text),
    }


def _scannable_lines(text: str) -> list[tuple[int, str, bool]]:
    result: list[tuple[int, str, bool]] = []
    fence: tuple[str, int] | None = None
    in_pre = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        lowered = stripped.casefold()
        if in_pre:
            result.append((line_number, line, False))
            if "</pre>" in lowered:
                in_pre = False
            continue
        if "<pre" in lowered:
            result.append((line_number, line, False))
            if "</pre>" not in lowered:
                in_pre = True
            continue
        fence_match = _FENCE_RE.match(line)
        if fence is not None:
            result.append((line_number, line, False))
            marker, minimum = fence
            if re.match(rf"^\s*{re.escape(marker)}{{{minimum},}}\s*$", line):
                fence = None
            continue
        if fence_match:
            token = fence_match.group(1)
            fence = (token[0], len(token))
            result.append((line_number, line, False))
            continue
        if line.startswith("    ") or line.startswith("\t"):
            result.append((line_number, line, False))
            continue
        result.append((line_number, line, True))
    return result


def _markdown_findings(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    hash_run: list[tuple[int, str]] = []

    def flush_hash_run() -> None:
        if not hash_run:
            return
        if len(hash_run) >= 2:
            line_number, line = hash_run[0]
            errors.append(
                _finding(
                    line=line_number,
                    code="TEXTILE_NUMBERED_LIST_IN_MARKDOWN",
                    message="Consecutive '# item' lines look like a Textile ordered list and render as Markdown headings.",
                    text=line,
                )
            )
        else:
            line_number, line = hash_run[0]
            warnings.append(
                _finding(
                    line=line_number,
                    code="AMBIGUOUS_HASH_LINE_IN_MARKDOWN",
                    message="A single '# text' line is valid Markdown but may be an intended Textile list item.",
                    text=line,
                )
            )
        hash_run.clear()

    for line_number, line, scannable in _scannable_lines(text):
        if not scannable:
            flush_hash_run()
            continue
        if _HASH_LINE_RE.match(line):
            hash_run.append((line_number, line))
        else:
            flush_hash_run()
        if _TEXTILE_HEADING_RE.match(line):
            errors.append(
                _finding(
                    line=line_number,
                    code="TEXTILE_HEADING_IN_MARKDOWN",
                    message="Textile heading syntax is not compatible with the configured Markdown renderer.",
                    text=line,
                )
            )
        if line.lstrip().startswith("|") and "|_." in line:
            errors.append(
                _finding(
                    line=line_number,
                    code="TEXTILE_TABLE_HEADER_IN_MARKDOWN",
                    message="Textile table header syntax is not compatible with the configured Markdown renderer.",
                    text=line,
                )
            )
    flush_hash_run()
    return errors, warnings


def validate_redmine_text(
    text: str,
    contract: dict[str, Any] | None,
    *,
    environment: str,
    field: str,
) -> dict[str, Any]:
    selected_environment = str(environment or "").strip().casefold()
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if contract is None:
        finding = _finding(
            line=0,
            code="TEXT_FORMAT_NOT_CONFIGURED",
            message="Redmine text format is not configured in the selected template.",
            text="",
        )
        if selected_environment == "corp":
            errors.append(finding)
        else:
            warnings.append(finding)
        return {
            "valid": not errors,
            "field": field,
            "engine": None,
            "validation": "unconfigured",
            "policy_version": TEXT_FORMAT_POLICY_VERSION,
            "errors": errors,
            "warnings": warnings,
        }

    engine = str(contract["engine"])
    validation = str(contract["validation"])
    policy_version = int(contract["policy_version"])
    if selected_environment == "corp" and validation != "strict":
        errors.append(
            _finding(
                line=0,
                code="STRICT_TEXT_FORMAT_REQUIRED",
                message="Corporate Redmine writes require strict text format validation.",
                text="",
            )
        )
    if validation == "off":
        warnings.append(
            _finding(
                line=0,
                code="TEXT_FORMAT_VALIDATION_DISABLED",
                message="Redmine text format validation is disabled by the selected template.",
                text="",
            )
        )
    elif engine == "markdown":
        detected_errors, detected_warnings = _markdown_findings(text)
        if validation == "strict":
            errors.extend(detected_errors)
        else:
            warnings.extend(detected_errors)
        warnings.extend(detected_warnings)
    return {
        "valid": not errors,
        "field": field,
        "engine": engine,
        "validation": validation,
        "policy_version": policy_version,
        "errors": errors,
        "warnings": warnings,
    }
