from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import RedmineMcpError


DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_AUDIT_DIR = "local/redmine_audit"
ENV_FILE_POINTER = "REDMINE_MCP_ENV_FILE"
COMPAT_ENV_FILE_POINTER = "TESTLINK_AGENT_ENV_FILE"
VALID_ENVIRONMENTS = {"corp", "sandbox"}


def parse_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        raise RedmineMcpError(f"Env file does not exist: {env_path}", code="ENV_FILE_NOT_FOUND")
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not os.environ.get(key, "").strip():
            os.environ[key] = value


def load_env_file(explicit_env_file: str | None = None) -> str | None:
    selected = (
        explicit_env_file
        or os.environ.get(ENV_FILE_POINTER, "").strip()
        or os.environ.get(COMPAT_ENV_FILE_POINTER, "").strip()
    )
    if selected:
        parse_env_file(selected)
        return selected
    for candidate in (Path(".env"), Path("local/redmine_mcp.env"), Path("local/testlink_agent.env")):
        if candidate.exists():
            parse_env_file(str(candidate))
            return str(candidate)
    return None


@dataclass(frozen=True)
class RedmineSettings:
    url: str
    api_key: str
    environment: str
    project_id: str = ""
    template_file: str = ""
    timeout: int = DEFAULT_TIMEOUT_SECONDS
    loaded_env_file: str | None = None


def load_redmine_settings(
    *,
    env_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> RedmineSettings:
    loaded = load_env_file(env_file)
    url = os.environ.get("REDMINE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("REDMINE_API_KEY", "").strip()
    environment = os.environ.get("REDMINE_ENV", "").strip().casefold()
    project_id = os.environ.get("REDMINE_PROJECT_ID", "").strip()
    template_file = os.environ.get("REDMINE_TEMPLATE", "").strip()
    if not url:
        raise RedmineMcpError("REDMINE_URL is required.", code="CONFIG_MISSING")
    if not api_key:
        raise RedmineMcpError("REDMINE_API_KEY is required.", code="CONFIG_MISSING")
    if environment not in VALID_ENVIRONMENTS:
        raise RedmineMcpError(
            "REDMINE_ENV must be explicitly set to corp or sandbox.",
            code="ENVIRONMENT_REQUIRED",
        )
    return RedmineSettings(
        url=url,
        api_key=api_key,
        environment=environment,
        project_id=project_id,
        template_file=template_file,
        timeout=timeout,
        loaded_env_file=loaded,
    )
