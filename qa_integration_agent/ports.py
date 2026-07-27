from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from testlink_agent_core.errors import redact_secrets

from .errors import CoordinatorError


QA_TESTLINK_ENV_POINTER = "QA_TESTLINK_MCP_ENV_FILE"
QA_REDMINE_ENV_POINTER = "QA_REDMINE_MCP_ENV_FILE"
QA_MCP_TIMEOUT = "QA_MCP_TIMEOUT_SECONDS"
_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class IntegrationPorts(Protocol):
    def testlink_execution(self, **kwargs: Any) -> dict[str, Any]: ...

    def redmine_bug(self, **kwargs: Any) -> dict[str, Any]: ...

    def redmine_comment(self, **kwargs: Any) -> dict[str, Any]: ...


class StdioMcpPorts:
    """Call ownership-specific MCP servers without loading their credentials in this process."""

    def __init__(
        self,
        *,
        testlink_env_file: str | None = None,
        redmine_env_file: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.testlink_env_file = str(
            testlink_env_file or os.environ.get(QA_TESTLINK_ENV_POINTER, "")
        ).strip()
        self.redmine_env_file = str(
            redmine_env_file or os.environ.get(QA_REDMINE_ENV_POINTER, "")
        ).strip()
        configured_timeout = str(os.environ.get(QA_MCP_TIMEOUT, "120")).strip()
        try:
            self.timeout = int(timeout if timeout is not None else configured_timeout)
        except (TypeError, ValueError) as exc:
            raise CoordinatorError("QA MCP timeout must be an integer.", code="MCP_CONFIG_INVALID") from exc
        if self.timeout < 1:
            raise CoordinatorError("QA MCP timeout must be positive.", code="MCP_CONFIG_INVALID")

    @staticmethod
    def _isolated_environment(pointer_name: str, pointer_value: str) -> dict[str, str]:
        if not pointer_value:
            raise CoordinatorError(
                f"Coordinator MCP env pointer is required: {pointer_name}",
                code="MCP_ENV_POINTER_REQUIRED",
            )
        pointer_path = Path(pointer_value)
        if not pointer_path.exists():
            raise CoordinatorError(
                f"Coordinator MCP env file does not exist: {pointer_path}",
                code="MCP_ENV_FILE_NOT_FOUND",
            )
        child_env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("TESTLINK_", "REDMINE_", "QA_TESTLINK_", "QA_REDMINE_"))
        }
        child_env[pointer_name] = str(pointer_path.resolve())
        if pointer_name == "TESTLINK_MCP_ENV_FILE":
            child_env["TESTLINK_MCP_TOOLSET"] = "integration"
        elif pointer_name == "REDMINE_MCP_ENV_FILE":
            child_env["REDMINE_MCP_TOOLSET"] = "integration"
        child_env.setdefault("PYTHONUTF8", "1")
        return child_env

    @staticmethod
    def _decode_tool_response(stdout: str, tool_name: str) -> dict[str, Any]:
        response: dict[str, Any] | None = None
        for line in reversed(str(stdout or "").splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                response = candidate
                break
        if response is None:
            raise CoordinatorError(f"{tool_name} MCP returned no JSON response.", code="MCP_INVALID_RESPONSE")
        if isinstance(response.get("error"), dict):
            message = str(response["error"].get("message") or response["error"])
            raise CoordinatorError(f"{tool_name} MCP error: {message}", code="MCP_REMOTE_ERROR")
        result = response.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        if not isinstance(content, list) or not content or not isinstance(content[0], dict):
            raise CoordinatorError(f"{tool_name} MCP returned no tool content.", code="MCP_INVALID_RESPONSE")
        try:
            payload = json.loads(str(content[0].get("text") or ""))
        except json.JSONDecodeError as exc:
            raise CoordinatorError(f"{tool_name} MCP content is not JSON.", code="MCP_INVALID_RESPONSE") from exc
        if not isinstance(payload, dict):
            raise CoordinatorError(f"{tool_name} MCP result must be an object.", code="MCP_INVALID_RESPONSE")
        return redact_secrets(payload)

    def _call(
        self,
        *,
        module: str,
        tool_name: str,
        arguments: dict[str, Any],
        env_pointer_name: str,
        env_pointer_value: str,
    ) -> dict[str, Any]:
        request = {
            "jsonrpc": "2.0",
            "id": f"qa-{tool_name}",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        child_env = self._isolated_environment(env_pointer_name, env_pointer_value)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", module],
                input=json.dumps(request, ensure_ascii=False) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=str(_REPOSITORY_ROOT),
                env=child_env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CoordinatorError(f"{tool_name} MCP timed out.", code="MCP_TIMEOUT", retryable=True) from exc
        except OSError as exc:
            raise CoordinatorError(f"{tool_name} MCP could not start: {exc}", code="MCP_START_FAILED") from exc
        if completed.returncode != 0:
            safe_stderr = str(redact_secrets(completed.stderr or "")).strip()
            raise CoordinatorError(
                f"{tool_name} MCP exited with code {completed.returncode}: {safe_stderr}",
                code="MCP_PROCESS_FAILED",
                retryable=True,
            )
        return self._decode_tool_response(completed.stdout, tool_name)

    def testlink_execution(self, **kwargs: Any) -> dict[str, Any]:
        return self._call(
            module="testlink_mcp.server",
            tool_name="testlink_report_execution",
            arguments=kwargs,
            env_pointer_name="TESTLINK_MCP_ENV_FILE",
            env_pointer_value=self.testlink_env_file,
        )

    def redmine_bug(self, **kwargs: Any) -> dict[str, Any]:
        return self._call(
            module="redmine_mcp.server",
            tool_name="redmine_create_bug",
            arguments=kwargs,
            env_pointer_name="REDMINE_MCP_ENV_FILE",
            env_pointer_value=self.redmine_env_file,
        )

    def redmine_comment(self, **kwargs: Any) -> dict[str, Any]:
        return self._call(
            module="redmine_mcp.server",
            tool_name="redmine_add_comment",
            arguments=kwargs,
            env_pointer_name="REDMINE_MCP_ENV_FILE",
            env_pointer_value=self.redmine_env_file,
        )
