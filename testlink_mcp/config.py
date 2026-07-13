from __future__ import annotations

import os
from dataclasses import dataclass

from testlink_agent_core.client import TestLinkClient
from testlink_agent_core.config import DEFAULT_TIMEOUT_SECONDS, TestLinkSettings, load_testlink_settings
from testlink_agent_core.errors import TestLinkError


VALID_ENVIRONMENTS = {"corp", "sandbox"}
DEFAULT_AUDIT_DIR = "local/testlink_audit"


@dataclass(frozen=True)
class TestLinkRuntime:
    settings: TestLinkSettings
    environment: str


def load_runtime(*, env_file: str | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> TestLinkRuntime:
    selected_env_file = env_file or os.environ.get("TESTLINK_MCP_ENV_FILE", "").strip() or None
    settings = load_testlink_settings(env_file=selected_env_file, timeout=timeout)
    environment = os.environ.get("TESTLINK_AGENT_PROFILE", "").strip().casefold()
    if environment not in VALID_ENVIRONMENTS:
        raise TestLinkError("TESTLINK_AGENT_PROFILE must be explicitly set to corp or sandbox.")
    return TestLinkRuntime(settings=settings, environment=environment)


def validate_environment(requested: str, configured: str) -> str:
    requested_env = str(requested or "").strip().casefold()
    if requested_env not in VALID_ENVIRONMENTS:
        raise TestLinkError("Requested TestLink environment must be corp or sandbox.")
    if requested_env != configured:
        raise TestLinkError(
            f"Requested TestLink environment does not match configured environment: "
            f"{requested_env} != {configured}"
        )
    return requested_env


def write_client(runtime: TestLinkRuntime) -> TestLinkClient:
    client = TestLinkClient(
        runtime.settings.url,
        runtime.settings.devkey,
        timeout=runtime.settings.timeout,
        max_retries=0,
    )
    if not client.check_devkey():
        raise TestLinkError("tl.checkDevKey failed.")
    return client
