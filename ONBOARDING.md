# TestLink Agent 同仁上手指南

這份指南帶你在自己的 Windows 電腦安裝 TestLink Agent 的 MCP 服務，並依工作情境設定給
Claude（Desktop 或 Claude Code）或 Codex 使用。

> 這個 repository 是公開的。公司內部的實際值（TestLink／Redmine 網址、bug 關聯端點名稱、
> 專案與欄位 ID）不寫在這裡，請向 TestLink 管理者或團隊內部文件取得，也不要貼到 issue、
> PR 或對話裡。

## 1. 先選情境

| 情境 | 適合誰 | 可以做的事 | 要註冊的服務 |
|---|---|---|---|
| `batch-import` | 自動化測試站台、批次回填結果 | 匯入自動化報表、建 build、回報結果、關聯 bug、新增／修改 TC、一次確認多筆結果 | `qa-integration-agent`、`testlink-mcp`、`redmine-mcp` |
| `manual-qa` | 手動或半自動測試、直接開 bug | 逐筆回報結果、關聯 bug、建 build、查 TC；預覽並建立 Redmine bug 與留言 | `testlink-mcp`、`redmine-mcp` |
| `bug-tracking` | 查詢與追蹤，不寫入 | 查 TC、查最新結果、搜尋與閱讀 Redmine issue | `testlink-mcp`、`redmine-mcp` |

不確定時先用 `bug-tracking`：它完全唯讀，之後再改設定即可。

所有寫入（回報結果、關聯 bug、建 build、改 TC、建 bug、留言）都會先產生預覽，你確認後
才會寫入，並在本機留下 audit 紀錄。agent 不會幫你關單或改 Redmine 狀態。

## 2. 事前準備

| 項目 | 取得方式 | 哪些情境需要 |
|---|---|---|
| Python 3.10 以上、`pipx`、`git` | 公司軟體安裝流程；`pipx` 可用 `python -m pip install --user pipx` | 全部 |
| TestLink 網址 | 向 TestLink 管理者取得 | 全部 |
| TestLink 個人 devKey | TestLink 網頁右上角「My Settings」→ API interface 產生 | 全部 |
| Redmine 網址 | 向 Redmine 管理者取得 | 全部 |
| Redmine 個人 API key | Redmine「My account」→ API access key → Show | 全部 |
| 你的 TestLink user ID（數字） | 見第 4.3 節 | `manual-qa`、`batch-import`（關聯 bug） |
| bug 關聯端點名稱 | 向 TestLink 管理者取得 | `manual-qa`、`batch-import`（關聯 bug） |
| TestLink 登入帳號 | 你自己的帳號 | `batch-import`（新增 TC） |
| Redmine 專案代號與 bug 範本 JSON | 團隊內部文件；範本格式見 `docs/redmine-template.example.json` | 要直接建 Redmine bug 時 |

devKey 和 API key 等同你的密碼：只放在本機 env 檔，不要寫進 Claude／Codex 設定、提示詞、
工具參數，也不要提交到任何 repository。

## 3. 安裝

使用 README「安裝」一節的固定版本 tag（不要直接安裝 `main`）：

```powershell
pipx install "git+https://github.com/sourceKang/UseTestlink.git@<版本 tag>"
```

安裝後會有這些執行檔（位於 `%USERPROFILE%\.local\bin`）：

- `testlink-mcp`、`redmine-mcp`、`qa-integration-agent-mcp`：MCP 服務
- `testlink-agent`：命令列工具，用來執行 `doctor` 健檢與安裝 skill

確認位置：

```powershell
Get-Command testlink-mcp, redmine-mcp, qa-integration-agent-mcp | Select-Object Source
```

### 3.1 安裝 skill（建議）

skill 是給 agent 看的操作步驟，例如「回報結果前先解析 target、先預覽再寫入」。安裝後，
Claude Code（包含 Claude Desktop 的 Code 分頁）在任何專案都會依情境自動載入：

```powershell
testlink-agent install-skills --dry-run
testlink-agent install-skills
```

- 會複製到 `%USERPROFILE%\.claude\skills\`，每個 skill 附上它引用的文件，不需要 clone 這個 repo。
- 已有同名、但不是這個工具安裝的 skill 會被略過並回報（退出碼 1），不會被覆蓋；確定要取代才加 `--force`。
- 安裝後重新啟動 Claude Code。

## 4. 建立本機設定檔

在 `%USERPROFILE%\.config\testlink-agent\` 建立兩個檔案（位置可自訂，但要用絕對路徑）。
TestLink 與 Redmine 的憑證必須分開放在不同檔案。

### 4.1 `testlink_mcp.env`

```text
TESTLINK_AGENT_PROFILE=corp
TESTLINK_URL=<TestLink 網址>
TESTLINK_DEVKEY=<你的 TestLink devKey>

# manual-qa、batch-import：關聯 bug 需要
TESTLINK_TESTER_ID=<你的 TestLink user ID，數字>
TESTLINK_BUG_LINK_ENDPOINT=<bug 關聯端點名稱，例如 name.php>

# batch-import：新增 TC 需要
TESTLINK_AUTHOR_LOGIN=<你的 TestLink 登入帳號>
```

- `TESTLINK_AGENT_PROFILE` 一定要明確設定：公司正式環境用 `corp`，自架測試環境用 `sandbox`。
- `TESTLINK_TESTER_ID` 是寫入 execution 時記錄的執行者，**請填你自己的 ID**。沒有預設值，
  沒填時關聯 bug 會回 `CONFIG_MISSING`，不會改用共用帳號。
- `TESTLINK_BUG_LINK_ENDPOINT` 是公司 TestLink 用來關聯 bug 的端點名稱，只填檔名（例如
  `name.php`），不能有路徑或參數。沒有預設值，沒填時同樣回 `CONFIG_MISSING`。
- `bug-tracking` 情境只需要前三行。

### 4.2 `redmine_mcp.env`

```text
REDMINE_ENV=corp
REDMINE_URL=<Redmine 網址>
REDMINE_API_KEY=<你的 Redmine API key>

# 要直接建 Redmine bug 時才需要
REDMINE_PROJECT_ID=<Redmine 專案代號>
REDMINE_TEMPLATE=<bug 範本 JSON 的絕對路徑>
```

### 4.3 找出自己的 TestLink user ID

1. 先用 `bug-tracking` 設定完成第 5、6 節，讓 agent 可以查 TestLink。
2. 找一個你最近在 TestLink 網頁上親自執行過的 TC，請 agent 用 `get_last_result` 查它在那個
   build 的最新結果。
3. 回傳的 `tester_id` 就是你的 user ID。若最新結果不是你執行的，換一筆，或直接問 TestLink
   管理者。
4. 填進 `TESTLINK_TESTER_ID`，再把情境改成 `manual-qa` 或 `batch-import`。

## 5. 註冊 MCP 服務

下面的 `<username>` 換成你的 Windows 帳號，env 檔路徑換成第 4 節的實際位置。
`*_TOOLSET` 決定 agent 看得到哪些工具；沒設定時只有唯讀工具。

### 5.1 Claude Desktop

設定檔在 `%APPDATA%\Claude\claude_desktop_config.json`。建議從 Claude 的
Settings → Developer → Edit Config 修改，存檔後**完全結束 Claude**（系統匣選結束）再開啟。
只合併 `mcpServers` 裡的項目，保留其他既有設定。

`manual-qa`（`bug-tracking` 把兩個 `manual-qa` 都改成 `bug-tracking`）：

```json
{
  "mcpServers": {
    "testlink-mcp": {
      "command": "C:/Users/<username>/.local/bin/testlink-mcp.exe",
      "env": {
        "PYTHONUTF8": "1",
        "TESTLINK_MCP_TOOLSET": "manual-qa",
        "TESTLINK_MCP_ENV_FILE": "C:/Users/<username>/.config/testlink-agent/testlink_mcp.env"
      }
    },
    "redmine-mcp": {
      "command": "C:/Users/<username>/.local/bin/redmine-mcp.exe",
      "env": {
        "PYTHONUTF8": "1",
        "REDMINE_MCP_TOOLSET": "manual-qa",
        "REDMINE_MCP_ENV_FILE": "C:/Users/<username>/.config/testlink-agent/redmine_mcp.env"
      }
    }
  }
}
```

`batch-import`：上面兩個服務的 toolset 改成 `batch-import`，再加上匯入協調服務：

```json
"qa-integration-agent": {
  "command": "C:/Users/<username>/.local/bin/qa-integration-agent-mcp.exe",
  "env": {
    "PYTHONUTF8": "1",
    "QA_INTEGRATION_TOOLSET": "import",
    "QA_TESTLINK_MCP_ENV_FILE": "C:/Users/<username>/.config/testlink-agent/testlink_mcp.env",
    "QA_REDMINE_MCP_ENV_FILE": "C:/Users/<username>/.config/testlink-agent/redmine_mcp.env"
  }
}
```

在 `batch-import` 下，`redmine-mcp` 只能查詢：自動化報表的 bug 由 `qa-integration-agent`
建立。如果你也要直接在對話裡建 Redmine bug，把 `REDMINE_MCP_TOOLSET` 改成 `manual-qa`。

Claude Desktop 的設定是所有專案共用的。若不同專案需要不同情境，改用 Claude Code 的專案設定。

### 5.2 Claude Code（依專案設定）

在專案根目錄的 `.mcp.json` 放入與上面相同的 `mcpServers` 內容，每個服務加上
`"type": "stdio"`。第一次使用時在該目錄執行 `claude`，接受工作目錄信任與 MCP 核准提示，
再用 `/mcp` 確認連線。`.mcp.json` 含有你的本機路徑，不要提交到共用 repository。
細節見 `docs/claude-setup.md`。

### 5.3 Codex

在 `%USERPROFILE%\.codex\config.toml` 加入（以 `manual-qa` 為例）：

```toml
[mcp_servers."testlink-mcp"]
command = 'C:\Users\<username>\.local\bin\testlink-mcp.exe'

[mcp_servers."testlink-mcp".env]
TESTLINK_MCP_TOOLSET = 'manual-qa'
TESTLINK_MCP_ENV_FILE = 'C:\Users\<username>\.config\testlink-agent\testlink_mcp.env'

[mcp_servers."redmine-mcp"]
command = 'C:\Users\<username>\.local\bin\redmine-mcp.exe'

[mcp_servers."redmine-mcp".env]
REDMINE_MCP_TOOLSET = 'manual-qa'
REDMINE_MCP_ENV_FILE = 'C:\Users\<username>\.config\testlink-agent\redmine_mcp.env'
```

改完重新啟動 Codex。

## 6. 驗證

### 6.1 離線健檢

健檢不會連線公司系統，也不會讀出憑證內容。toolset 填你在設定裡用的值：

```powershell
$env:TESTLINK_MCP_TOOLSET = "manual-qa"
testlink-agent doctor --server testlink `
  --executable "C:/Users/<username>/.local/bin/testlink-mcp.exe" `
  --testlink-env-file "C:/Users/<username>/.config/testlink-agent/testlink_mcp.env"
```

```powershell
$env:REDMINE_MCP_TOOLSET = "manual-qa"
testlink-agent doctor --server redmine `
  --executable "C:/Users/<username>/.local/bin/redmine-mcp.exe" `
  --redmine-env-file "C:/Users/<username>/.config/testlink-agent/redmine_mcp.env"
```

`batch-import` 另外檢查匯入協調服務：

```powershell
$env:QA_INTEGRATION_TOOLSET = "import"
testlink-agent doctor --server qa `
  --executable "C:/Users/<username>/.local/bin/qa-integration-agent-mcp.exe" `
  --testlink-env-file "C:/Users/<username>/.config/testlink-agent/testlink_mcp.env" `
  --redmine-env-file "C:/Users/<username>/.config/testlink-agent/redmine_mcp.env"
```

退出碼 0 表示沒有錯誤；warning 也要看一下（例如 toolset 用了 `all`）。

### 6.2 第一次實際使用（唯讀）

重開 Claude 或 Codex 後，請 agent 做一次唯讀查詢，例如：

> 用 testlink_resolve_execution_target 解析 project「…」、plan「…」、platform「…」、build「…」

能回傳 project、plan、platform、build 的 ID，就代表 TestLink 連線與設定正確。
Redmine 可以請 agent 用 `redmine_get_issue` 讀一張你知道的 issue。

## 7. 日常使用須知

- **寫入一律先預覽**：檢查 target、內容、副作用與 digest 後，明確回覆確認，agent 才會用同一份
  預覽寫入。內容改了就要重新預覽。
- **關聯 bug（`testlink_link_bug`）的副作用**：一定會新增一筆 execution；同一張 bug 在同一個
  build 第一次關聯時，Redmine 會被自動留言；伺服器不會回報關聯是否成功，寫入結果只會是
  「已送出」，請到 TestLink 頁面確認。關聯錯了只能在 TestLink 網頁上手動移除。
- **確認多筆結果**：用 `testlink_get_latest_results`（搭配 `expected_status`），一次呼叫只列出不符合
  的 TC，不要逐筆查。
- **audit 路徑**：從 Claude Desktop 寫入時，`audit_dir` 要給可寫入的絕對路徑；Desktop 啟動 MCP
  的工作目錄不可寫。
- agent 不會關單、改狀態、改 assignee 或 fixed version，這些仍由你在 TestLink／Redmine 操作。

## 8. 升級

1. **先完全結束 Claude 和 Codex**。正在執行的舊版服務會鎖住執行檔，`pipx` 會因
   `PermissionError: [WinError 5]` 中止（此時舊版仍完整可用）。
2. 安裝新的 tag：

   ```powershell
   pipx install --force "git+https://github.com/sourceKang/UseTestlink.git@<新版本 tag>"
   ```

3. 執行 `testlink-agent install-skills`，把 skill 更新成新版內容。
4. 重新執行第 6.1 節的健檢，再開啟 Claude／Codex。

## 9. 疑難排解

| 狀況 | 原因與處理 |
|---|---|
| 看不到某個工具 | toolset 不包含它；確認 `*_TOOLSET` 的值，改完要重啟用戶端 |
| `CONFIG_MISSING`（`TESTLINK_TESTER_ID` 或 `TESTLINK_BUG_LINK_ENDPOINT`） | env 檔缺少該行，補上後重啟 |
| `TESTLINK_AGENT_PROFILE must be explicitly set` | env 檔沒有設定 `corp` 或 `sandbox` |
| `ENVIRONMENT_REQUIRED`（Redmine） | `redmine_mcp.env` 缺少 `REDMINE_ENV` |
| 寫入時 `[WinError 5]` 或路徑在 `System32` | `audit_dir` 沒給可寫入的絕對路徑 |
| Claude Code 顯示 `Pending approval` | 尚未核准專案 MCP；在該目錄執行 `claude` 接受提示 |
| 改了 Desktop 設定卻沒生效，或設定被還原 | 要完全結束 Claude 再開；Claude 執行中改檔可能被覆蓋，建議從 Edit Config 修改 |

更多細節：`docs/claude-setup.md`（Claude 接入）、`docs/testlink-mcp-v2.md`（TestLink 工具）、
`docs/redmine-mcp.md`（Redmine 工具）、`docs/deployment.md`（安裝與升級）。
