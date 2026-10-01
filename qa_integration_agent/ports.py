from __future__ import annotations

import contextlib
import itertools
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Protocol

from testlink_agent_core.errors import redact_secrets

from .errors import CoordinatorError


QA_TESTLINK_ENV_POINTER = "QA_TESTLINK_MCP_ENV_FILE"
QA_REDMINE_ENV_POINTER = "QA_REDMINE_MCP_ENV_FILE"
QA_MCP_TIMEOUT = "QA_MCP_TIMEOUT_SECONDS"
TESTLINK_BATCH_SESSION_ENV = "TESTLINK_MCP_BATCH_SESSION"
STDERR_TAIL_LINES = 50


class IntegrationPorts(Protocol):
    def testlink_execution(self, **kwargs: Any) -> dict[str, Any]: ...

    def redmine_bug(self, **kwargs: Any) -> dict[str, Any]: ...

    def redmine_comment(self, **kwargs: Any) -> dict[str, Any]: ...


class _SessionChild:
    """One ownership-specific MCP child kept open for a single coordinator batch.

    Requests are line-delimited JSON-RPC, exactly as the one-shot path sends
    them, so the child keeps its own credentials, toolset and audit. Pipes are
    drained by threads because Windows pipes cannot be polled with select().
    """

    def __init__(self, module: str, child_env: dict[str, str]) -> None:
        self.module = module
        self.process = subprocess.Popen(
            [sys.executable, "-m", module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.stderr_tail: list[str] = []
        self.request_ids = itertools.count(1)
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        self.stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
        self.stderr_thread.start()

    def _pump_stdout(self) -> None:
        stream = self.process.stdout
        if stream is not None:
            with stream:
                for line in stream:
                    self.lines.put(line)
        self.lines.put(None)

    def _pump_stderr(self) -> None:
        stream = self.process.stderr
        if stream is None:
            return
        with stream:
            for line in stream:
                self.stderr_tail.append(line.rstrip("\n"))
                del self.stderr_tail[:-STDERR_TAIL_LINES]

    def safe_stderr(self) -> str:
        return str(redact_secrets("\n".join(self.stderr_tail))).strip()

    def call(self, tool_name: str, arguments: dict[str, Any], timeout: int) -> str:
        request_id = f"qa-{tool_name}-{next(self.request_ids)}"
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        stdin = self.process.stdin
        try:
            if stdin is None:
                raise BrokenPipeError("child stdin is closed")
            stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            stdin.flush()
        except OSError as exc:
            raise CoordinatorError(
                f"{tool_name} MCP session is not accepting requests: {self.safe_stderr()}",
                code="MCP_PROCESS_FAILED",
                retryable=True,
            ) from exc
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CoordinatorError(f"{tool_name} MCP timed out.", code="MCP_TIMEOUT", retryable=True)
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                code = self.process.wait()
                self.stderr_thread.join(timeout=1)
                raise CoordinatorError(
                    f"{tool_name} MCP exited with code {code}: {self.safe_stderr()}",
                    code="MCP_PROCESS_FAILED",
                    retryable=True,
                )
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get("id") == request_id:
                return line

    def _close_stdin(self) -> None:
        with contextlib.suppress(OSError):
            if self.process.stdin is not None:
                self.process.stdin.close()

    def kill(self) -> None:
        with contextlib.suppress(OSError):
            self.process.kill()
        self._close_stdin()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.process.wait(timeout=10)

    def close(self) -> None:
        """End of batch: EOF on stdin lets the server loop return normally."""
        self._close_stdin()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.kill()


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
        self._session_children: dict[str, _SessionChild] | None = None

    @contextlib.contextmanager
    def session(self) -> Iterator["StdioMcpPorts"]:
        """Reuse one child per MCP server for every call made inside this block.

        Outside a session each call still starts and ends its own child. Nested
        sessions share the outer one; every child is closed when it ends.
        """
        if self._session_children is not None:
            yield self
            return
        self._session_children = {}
        try:
            yield self
        finally:
            children, self._session_children = self._session_children, None
            for child in children.values():
                child.close()

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
        if self._session_children is not None:
            return self._session_call(module=module, tool_name=tool_name, arguments=arguments, child_env=child_env)
        try:
            # No explicit cwd: the child inherits this process's working directory,
            # which the MCP client chooses (C:\Windows\System32 under Claude Desktop).
            # Writes therefore never rely on the child's relative default audit
            # path; the coordinator passes an absolute audit_dir on every write.
            completed = subprocess.run(
                [sys.executable, "-m", module],
                input=json.dumps(request, ensure_ascii=False) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
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

    def _session_call(
        self,
        *,
        module: str,
        tool_name: str,
        arguments: dict[str, Any],
        child_env: dict[str, str],
    ) -> dict[str, Any]:
        assert self._session_children is not None
        child = self._session_children.get(module)
        if child is None:
            if module == "testlink_mcp.server":
                child_env[TESTLINK_BATCH_SESSION_ENV] = "1"
            try:
                # Same cwd inheritance as the one-shot path; writes carry an absolute audit_dir.
                child = _SessionChild(module, child_env)
            except OSError as exc:
                raise CoordinatorError(f"{tool_name} MCP could not start: {exc}", code="MCP_START_FAILED") from exc
            self._session_children[module] = child
        try:
            line = child.call(tool_name, arguments, self.timeout)
        except CoordinatorError:
            # A timed-out or dead child is never reused; the next call starts a fresh one.
            self._session_children.pop(module, None)
            child.kill()
            raise
        return self._decode_tool_response(line, tool_name)

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
