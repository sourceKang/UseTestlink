from __future__ import annotations

import json
import sys
from typing import Any

from testlink_agent_core.errors import normalize_testlink_error, redact_secrets

from . import __version__
from .api import call_tool
from .config import load_runtime, write_client
from .tools import TOOLS


def _result_response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": str(redact_secrets(message))}
    if data is not None:
        error["data"] = redact_secrets(data)
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if request_id is None and str(method).startswith("notifications/"):
        return None
    if method == "initialize":
        return _result_response(
            request_id,
            {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "testlink-mcp", "version": __version__},
            },
        )
    if method == "ping":
        return _result_response(request_id, {})
    if method == "tools/list":
        return _result_response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        result = call_tool(
            str(params.get("name")),
            params.get("arguments") if isinstance(params.get("arguments"), dict) else {},
        )
        return _result_response(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(redact_secrets(result), indent=2, ensure_ascii=False, default=str),
                    }
                ],
                "isError": not bool(result.get("ok")),
            },
        )
    return _error_response(request_id, -32601, f"Method not found: {method}")


def _read_message() -> tuple[dict[str, Any] | None, str]:
    first_line = sys.stdin.buffer.readline()
    if not first_line:
        return None, "line"
    if first_line.lower().startswith(b"content-length:"):
        headers = [first_line]
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None, "content-length"
            if line in (b"\r\n", b"\n"):
                break
            headers.append(line)
        content_length = 0
        for header in headers:
            name, _, value = header.decode("ascii", errors="replace").partition(":")
            if name.casefold() == "content-length":
                content_length = int(value.strip())
                break
        return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8")), "content-length"
    line = first_line.decode("utf-8").strip()
    return (json.loads(line), "line") if line else ({}, "line")


def _write_response(response: dict[str, Any], framing: str) -> None:
    payload = json.dumps(redact_secrets(response), ensure_ascii=False).encode("utf-8")
    if framing == "content-length":
        sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return
    print(payload.decode("utf-8"), flush=True)


def startup_health_check() -> dict[str, Any]:
    runtime = load_runtime()
    client = write_client(runtime)
    return {
        "server": "testlink-mcp",
        "version": __version__,
        "environment": runtime.environment,
        "authenticated": True,
        "testlink_about": client.about(),
    }


def run(*, health_check: bool = True) -> int:
    if health_check:
        health = startup_health_check()
        print(f"testlink-mcp v{__version__}", file=sys.stderr, flush=True)
        print(json.dumps(redact_secrets({"health": health}), ensure_ascii=False), file=sys.stderr, flush=True)
    while True:
        try:
            message, framing = _read_message()
            if message is None:
                break
            if not message:
                continue
            response = handle_request(message)
        except Exception as exc:
            framing = "line"
            normalized = normalize_testlink_error(exc)
            response = _error_response(None, -32603, normalized.message, normalized.to_dict())
        if response is not None:
            _write_response(response, framing)
    return 0


def main() -> int:
    try:
        return run(health_check=True)
    except Exception as exc:
        normalized = normalize_testlink_error(exc)
        print(f"testlink-mcp v{__version__} startup failed: {normalized.message}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
