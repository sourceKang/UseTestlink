from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .errors import TestLinkError


DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_CATALOG_PATH = "local/testlink_catalog.json"
DEFAULT_ENV_FILE_PATH = "local/testlink_agent.env"
DEFAULT_PROFILES_PATH = "local/testlink_profiles.json"
ENV_FILE_POINTER = "TESTLINK_AGENT_ENV_FILE"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

STATUS_TO_TESTLINK = {
    "pass": "p",
    "fail": "f",
    "blocked": "b",
    "error": "f",
}

IMPORTANCE_TO_TESTLINK = {
    "low": 1,
    "medium": 2,
    "high": 3,
}

EXECUTION_TYPE_TO_TESTLINK = {
    "manual": 1,
    "automated": 2,
}


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
        if not os.environ.get(key, "").strip():
            os.environ[key] = value


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _project_root_env_candidates() -> list[Path]:
    root_candidates = [
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / DEFAULT_ENV_FILE_PATH,
    ]
    cwd_candidates = [Path(".env"), Path(DEFAULT_ENV_FILE_PATH)]
    unique: list[Path] = []
    for candidate in root_candidates:
        if any(_same_path(candidate, cwd_candidate) for cwd_candidate in cwd_candidates):
            continue
        unique.append(candidate)
    return unique


def load_env_files(explicit_env_file: str | None) -> list[str]:
    if explicit_env_file:
        parse_env_file(explicit_env_file)
        return [explicit_env_file]

    loaded: list[str] = []
    pointer = os.environ.get(ENV_FILE_POINTER, "").strip()
    if pointer:
        parse_env_file(pointer)
        return [pointer]

    candidates = [Path(".env"), Path(DEFAULT_ENV_FILE_PATH)]
    if not any(candidate.exists() for candidate in candidates):
        candidates.extend(_project_root_env_candidates())

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            parse_env_file(str(path))
            loaded.append(str(path))
    return loaded


@dataclass(frozen=True)
class TestLinkSettings:
    url: str
    devkey: str
    timeout: int = DEFAULT_TIMEOUT_SECONDS
    loaded_env_files: tuple[str, ...] = ()


def load_testlink_settings(
    *,
    env_file: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> TestLinkSettings:
    loaded = load_env_files(env_file)
    base_url = os.environ.get("TESTLINK_URL", "").strip()
    devkey = os.environ.get("TESTLINK_DEVKEY", "").strip()
    if not base_url:
        raise TestLinkError("TESTLINK_URL is required.")
    if not devkey:
        raise TestLinkError("TESTLINK_DEVKEY is required.")
    return TestLinkSettings(
        url=base_url,
        devkey=devkey,
        timeout=timeout,
        loaded_env_files=tuple(loaded),
    )


def catalog_path(value: str | None = None) -> Path:
    return Path(value or DEFAULT_CATALOG_PATH)
