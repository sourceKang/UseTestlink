from __future__ import annotations

import os
from typing import Any

from .config import VALID_ENVIRONMENTS
from .errors import RedmineMcpError


MANAGER_ONLY_FIELDS = ("assigned_to_id", "fixed_version_id")


def validate_environment(requested: str, configured: str) -> str:
    requested_env = str(requested or "").strip().casefold()
    configured_env = str(configured or "").strip().casefold()
    if requested_env not in VALID_ENVIRONMENTS:
        raise RedmineMcpError(
            "Requested environment must be explicitly set to corp or sandbox.",
            code="ENVIRONMENT_REQUIRED",
        )
    if configured_env not in VALID_ENVIRONMENTS:
        raise RedmineMcpError("Configured Redmine environment is invalid.", code="CONFIG_INVALID")
    if requested_env != configured_env:
        raise RedmineMcpError(
            f"Requested Redmine environment does not match configured environment: "
            f"{requested_env} != {configured_env}",
            code="ENVIRONMENT_MISMATCH",
        )
    return requested_env


def manager_fields_allowed() -> bool:
    return os.environ.get("REDMINE_ALLOW_MANAGER_FIELDS", "").strip().casefold() in {"1", "true", "yes", "on"}


def blocked_manager_fields(issue_payload: dict[str, Any]) -> list[str]:
    if manager_fields_allowed():
        return []
    return [field for field in MANAGER_ONLY_FIELDS if issue_payload.get(field) not in (None, "")]
