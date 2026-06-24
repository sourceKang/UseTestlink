from __future__ import annotations

import json
import sys
from typing import Any

from . import __version__
from .api import call_tool
from .client import TestLinkClient
from .config import load_testlink_settings
from .errors import normalize_testlink_error, redact_secrets
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
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        result = call_tool(str(tool_name), arguments if isinstance(arguments, dict) else {})
        return _result_response(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False, default=str)}],
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
        body = sys.stdin.buffer.read(content_length)
        return json.loads(body.decode("utf-8")), "content-length"

    line = first_line.decode("utf-8").strip()
    if not line:
        return {}, "line"
    return json.loads(line), "line"


def _write_response(response: dict[str, Any], framing: str) -> None:
    payload = json.dumps(redact_secrets(response), ensure_ascii=False).encode("utf-8")
    if framing == "content-length":
        sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        return
    print(payload.decode("utf-8"), flush=True)


def startup_health_check() -> dict[str, Any]:
    settings = load_testlink_settings()
    client = TestLinkClient(settings.url, settings.devkey, timeout=settings.timeout, max_retries=0)
    if not client.check_devkey():
        raise RuntimeError("tl.checkDevKey failed.")
    about = client.about()
    return {
        "server": "testlink-mcp",
        "version": __version__,
        "check_devkey": True,
        "testlink_about": about,
    }


def run(*, health_check: bool = True) -> int:
    if health_check:
        health = startup_health_check()
        print(f"testlink-mcp v{__version__}", file=sys.stderr, flush=True)
        print(json.dumps(redact_secrets({"health": health}), ensure_ascii=False, default=str), file=sys.stderr, flush=True)

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
        if normalized.possible_causes:
            print(json.dumps({"possible_causes": normalized.possible_causes}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
