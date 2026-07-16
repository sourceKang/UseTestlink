from __future__ import annotations

import json
import sys
from typing import Any

from testlink_agent_core.errors import redact_secrets

from . import __version__
from .api import call_tool
from .errors import normalize_error
from .tools import TOOLS


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": str(redact_secrets(message))}
    if data is not None:
        payload["data"] = redact_secrets(data)
    return {"jsonrpc": "2.0", "id": request_id, "error": payload}


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if request_id is None and str(method).startswith("notifications/"):
        return None
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "qa-integration-agent", "version": __version__},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        result = call_tool(
            str(params.get("name")),
            params.get("arguments") if isinstance(params.get("arguments"), dict) else {},
        )
        return _result(
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
    return _error(request_id, -32601, f"Method not found: {method}")


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
        length = 0
        for header in headers:
            name, _, value = header.decode("ascii", errors="replace").partition(":")
            if name.casefold() == "content-length":
                length = int(value.strip())
                break
        return json.loads(sys.stdin.buffer.read(length).decode("utf-8")), "content-length"
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


def run() -> int:
    print(f"qa-integration-agent v{__version__}", file=sys.stderr, flush=True)
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
            safe_error = normalize_error(exc, "mcp-server")
            response = _error(None, -32603, str(safe_error.get("message") or "Internal error"), safe_error)
        if response is not None:
            _write_response(response, framing)
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
