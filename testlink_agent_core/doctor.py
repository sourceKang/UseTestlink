"""Offline diagnostics: never load credential contents or call upstream systems."""
from __future__ import annotations

import json
import os
import shutil
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from .errors import redact_secrets


SERVERS = {
    "qa": ("qa-integration-agent-mcp", "QA_INTEGRATION_TOOLSET", "import"),
    "testlink": ("testlink-mcp", "TESTLINK_MCP_TOOLSET", "all"),
    "redmine": ("redmine-mcp", "REDMINE_MCP_TOOLSET", "all"),
}
RECOMMENDED_TOOLSETS = {"qa": "import", "testlink": "execution", "redmine": "issue"}


def diagnose(*, server: str = "qa", testlink_env_file: str | None = None,
             redmine_env_file: str | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, status: str, message: str, fix: str = "", **details: Any) -> None:
        checks.append({"check": name, "status": status, "message": message, "fix": fix, **details})

    if server not in SERVERS:
        add("server", "error", "未知服務。", "選擇 qa、testlink 或 redmine。")
        return {"ok": False, "offline": True, "checks": checks}
    add("python", "ok" if sys.version_info >= (3, 10) else "error",
        "需要 Python 3.10 以上。", version=".".join(map(str, sys.version_info[:3])))
    try:
        distribution = metadata.distribution("testlink-agent")
        package_root = Path(distribution.locate_file("testlink_agent_core")).resolve()
        current_root = Path(__file__).resolve().parent
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        editable = bool(direct.get("dir_info", {}).get("editable"))
        status = "warning" if editable or package_root != current_root else "ok"
        add("package", status, "已找到套件中繼資料；套件版本與各 MCP 元件版本可不同。",
            "共用安裝請使用已審查 tag 的隔離環境，避免 editable 或 checkout 遮蔽。" if status == "warning" else "",
            version=distribution.version, editable=editable, source_matches=package_root == current_root)
    except metadata.PackageNotFoundError:
        add("package", "error", "未找到已安裝的 testlink-agent 套件。", "依 docs/deployment.md 安裝已審查版本。")
    except (OSError, ValueError, TypeError, AttributeError):
        add("package", "error", "無法驗證套件中繼資料。", "重新安裝已審查版本。")

    executable, toolset_key, default_toolset = SERVERS[server]
    resolved = shutil.which(executable)
    add("executable", "ok" if resolved else "error", "檢查服務執行檔是否位於 PATH。",
        "確認安裝與 PATH，或在客戶端設定執行檔絕對路徑。" if not resolved else "",
        executable=executable, path=resolved)

    from qa_integration_agent.tools import tools_for_toolset as qa_tools
    from redmine_mcp.tools import tools_for_toolset as redmine_tools
    from testlink_mcp.tools import tools_for_toolset as testlink_tools

    selected = os.environ.get(toolset_key, default_toolset).strip().casefold()
    try:
        if not selected:
            raise ValueError("empty toolset")
        selected_tools = {"qa": qa_tools, "testlink": testlink_tools, "redmine": redmine_tools}[server](selected)
        add("toolset", "warning" if selected == "all" else "ok", "已確認本次環境的工具集合。",
            f"建議依任務設定 {toolset_key}={RECOMMENDED_TOOLSETS[server]}。" if selected == "all" else "",
            toolset=selected, tools=[tool["name"] for tool in selected_tools])
    except ValueError:
        add("toolset", "error", "工具集合設定無效。", f"檢查 {toolset_key}；建議值為 {RECOMMENDED_TOOLSETS[server]}。")

    pointers = []
    if server in ("qa", "testlink"):
        key = "QA_TESTLINK_MCP_ENV_FILE" if server == "qa" else "TESTLINK_MCP_ENV_FILE"
        pointers.append((key, testlink_env_file if testlink_env_file is not None else os.environ.get(key, "")))
    if server in ("qa", "redmine"):
        key = "QA_REDMINE_MCP_ENV_FILE" if server == "qa" else "REDMINE_MCP_ENV_FILE"
        pointers.append((key, redmine_env_file if redmine_env_file is not None else os.environ.get(key, "")))
    for key, value in pointers:
        try:
            if not value or not Path(value).is_absolute():
                add(key, "error", "缺少憑證檔絕對路徑。", f"設定 {key} 或對應的 --*-env-file 選項。")
                continue
            if not Path(value).is_file():
                add(key, "error", "憑證路徑不是既有檔案。", "檢查本機檔案位置；不要將憑證放進工具參數或 Git。")
                continue
            with Path(value).open("rb"):
                pass  # Check open permission only; never read credentials.
            add(key, "ok", "檔案存在且可開啟；未讀取內容，未驗證憑證有效性。")
        except (OSError, ValueError):
            add(key, "error", "無法開啟憑證檔。", "檢查路徑格式與目前使用者的讀取權限。")
    return redact_secrets({
        "ok": not any(check["status"] == "error" for check in checks),
        "offline": True,
        "server": server,
        "checks": checks,
        "scope": "只檢查本次程序環境；不讀取 Claude/Codex 設定，不驗證憑證內容或遠端連線。",
    })


def command_doctor(args: Any) -> int:
    result = diagnose(server=args.server, testlink_env_file=args.testlink_env_file,
                      redmine_env_file=args.redmine_env_file)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["checks"]:
            print(f"[{check['status']}] {check['check']}: {check['message']}")
            for field in ("version", "path", "toolset", "tools"):
                if field in check:
                    print(f"  {field}: {check[field]}")
            if check["fix"]:
                print(f"  修正：{check['fix']}")
        print(result["scope"])
    return 0 if result["ok"] else 1
