# Claude 接入與離線健檢

本專案透過本機 stdio MCP 供 Claude Code 與 Claude Desktop 使用，
沿用相同的 TestLink／Redmine／QA 責任邊界。

## 版本與驗證範圍

`doctor` 與 `qa_read_preview_artifact` 從 v1.8.0 tag 開始提供，v1.7.0 以前的 tag 沒有這兩項功能。
共用環境依 `deployment.md` 安裝 v1.8.0 以上的 tag。不要將其他專案指向開發 checkout。
交接報告已提供 commit `a6e7c01` 在 Linux／Python 3.11、Claude Code 2.1.274
非互動模式的實際 MCP 接入證據（合成 artifact 分頁、中文及拒絕情境）。
同次測試發現內層 JSON 遮蔽缺陷，本批修正另以離線回歸驗證；
不可將舊 commit 的應用程式實測視為修正版已重測。
Windows Claude Code、Claude Desktop、真實 preview 產生及外部寫入仍未實測。

## 1. 準備本機設定

第一次為自己設定的同仁，請先看 repository 根目錄的 `ONBOARDING.md`：依情境說明安裝、env 檔、
註冊、健檢與升級。本文件是 Claude 接入的細節參考。

安裝套件後執行 `testlink-agent install-skills`，把 agent skill 複製到 `~/.claude/skills`，
Claude Code（含 Desktop 的 Code 分頁）就能在任何專案依任務載入；升級後要再執行一次。


1. 依 `deployment.md` 安裝經審查的發布版本。
2. 用 `Get-Command qa-integration-agent-mcp | Select-Object Source` 找到實際執行檔。
3. 保留既有、各自獨立的 TestLink 與 Redmine 憑證檔；使用絕對路徑。
   範本中的 `.config/testlink-agent` 只是建議位置，不必搬移現有檔案。
4. 把範本的 `<username>`、執行檔與憑證檔位置換成實際值。
   憑證本身不得寫進 JSON、提示詞或工具參數。

範本只有一個 `qa-integration-agent`，工具集合為 `import`。
單獨維護 testcase 或查詢 Redmine 時，依 README 的直接操作流程切換服務，
不要預設同時註冊所有服務。

### 依使用情境選擇工具組合

每個服務的 `*_TOOLSET` 可填一個名稱或用逗號分隔的多個名稱，三種情境可用同一個預設名稱：

| 情境 | 註冊的服務與設定 | 用途 |
|---|---|---|
| `batch-import` | `qa-integration-agent`（`import`）、`testlink-mcp`、`redmine-mcp` 皆設 `batch-import` | 自動化報表匯入、建 build、維護 testcase；Redmine 只讀，bug 由匯入流程建立 |
| `manual-qa` | `testlink-mcp`、`redmine-mcp` 皆設 `manual-qa`（不註冊 qa-integration-agent） | 逐筆回報結果、關聯 bug、直接預覽與建立 Redmine bug／留言 |
| `bug-tracking` | `testlink-mcp`、`redmine-mcp` 皆設 `bug-tracking` | 只查詢與追蹤，不寫入 |

testlink-mcp 與 redmine-mcp 未設定時只提供唯讀的 `discovery`；要寫入必須明確選擇。
`all` 會帶入 `delete_execution` 等舊工具，`doctor` 會對它提出 warning。
Desktop 的設定是全域的，若不同專案需要不同情境，Claude Code 可改在各專案的 `.mcp.json` 設定（見下節）。

## 2. Claude Code

將 `claude-code-mcp.example.json` 的 `mcpServers` 項目合併到使用專案的
`.mcp.json`，保留既有其他服務，不要整份覆蓋。也可依官方文件採用個人範圍註冊。
使用者機器的實際路徑不應提交到共用 repository；此 repository 已忽略 `.mcp.json`。
首次使用專案 `.mcp.json` 時，若 `claude mcp list` 顯示 `Pending approval`，
代表尚未核准，不能當作連線故障。請在該目錄互動執行 `claude`，
檢查並接受工作目錄信任與這個 MCP 的核准提示，再用 `/mcp` 查看連線與工具清單。

已信任的測試專案若需非互動操作，可由使用者在本機
`.claude/settings.local.json` 合併以下設定，只核准本服務：

```json
{
  "enabledMcpjsonServers": ["qa-integration-agent"]
}
```

保留既有設定與其他已核准項目，不要整份覆蓋。
`enableAllProjectMcpServers: true` 會核准專案中的所有 MCP，只有已審閱全部服務的
隔離測試環境才考慮採用，本專案不預設啟用。
本機核准設定不代表跳過工作目錄信任；未信任目錄可能仍需互動核准。
CI 應由管理者明確提供經審閱的設定，不由下載的 repository 自行核准服務。
本 repository 已忽略 `.claude/`，不得提交個人核准設定或真實路徑。
核准 MCP 載入也不等於授權外部寫入；write 與 Redmine opt-in 規則照常生效。

參考：[官方 MCP 信任與核准說明](https://code.claude.com/docs/en/mcp#project-server-approvals-and-workspace-trust)。

本 repository 的 `CLAUDE.md` 透過 `@AGENTS.md` 匯入共用安全規則，
無須維護第二份相同守則。這是 Claude Code 開發本 repository 的入口；
在其他 repository 使用 MCP 時，仍需提供相同的預覽／確認工作流程指示。

## 3. Claude Desktop

將 `claude-desktop-config.example.json` 的服務項目合併到 Desktop 的
`claude_desktop_config.json`。Windows 一般位置是
`%APPDATA%/Claude/claude_desktop_config.json`；以應用程式設定所開啟的位置為準。
完成後重新啟動 Desktop，查看服務與工具是否可用。

Desktop 不會因註冊 MCP 自動讀取 repository 的 `CLAUDE.md`。
請將下節工作流程加入使用該工具的專案指示。單純連上 MCP 不等於授權寫入。

## 4. 共用工作流程

- 回覆使用繁體中文；先取得明確 environment、project、plan、platform、build、report。
- 報表使用 MCP 所在電腦的絕對路徑；聊天上傳的附件不會自動變成該路徑。
  Claude Desktop 啟動 MCP 時的工作目錄是 `C:\Windows\System32`（不可寫），相對路徑會落在那裡而失敗。
  未指定 `artifact_dir`／`audit_dir` 時，預設使用每位使用者的絕對路徑
  `%LOCALAPPDATA%\testlink-agent\{qa_previews,qa_audit,testlink_audit,redmine_audit}`
  （非 Windows 為 `~/.testlink-agent/...`），可用 `TESTLINK_AGENT_DATA_DIR` 改變根目錄；
  v1.9.6 以前預設是相對的 `local/...`，在 Claude Desktop 中必須自行傳入絕對路徑。
  v1.8.1 起，TestLink／Redmine child audit 會自動寫在 `<audit_dir>/testlink`、`<audit_dir>/redmine`；
  v1.8.0 以前從 Claude Desktop 執行寫入會因 System32 不可寫而失敗。
- 直接呼叫 `redmine_preview_bug`／`redmine_create_bug` 時，v1.8.2 起 `custom_fields` 依 ID
  覆蓋 template 欄位並保留其餘預設值；template 中 `{{...}}` token 欄位須由呼叫端傳入已渲染值，
  否則 `TEMPLATE_UNRESOLVED` 會列出未解析的欄位名稱與 ID。
- 直接註冊 `testlink-mcp` 或 `redmine-mcp` 時，請在 `env` 依上方情境表明確設定 `*_TOOLSET`
  （例如 `"manual-qa"`）。未設定時只提供唯讀的 `discovery`；v1.9.0 以前未設定會開放 `all`，
  testlink 包含 `delete_execution` 等舊工具，redmine 可直接建 bug 與留言。
- 回填後抽查或確認多筆結果時，用 `testlink_get_latest_results`（`all_in_plan=true` 或列出 TC，搭配
  `expected_status`），一次呼叫只列出不符合的項目，不要逐筆呼叫 `get_last_result`。
- 建 build 請用 `testlink_create_build`（在 `execution`、`manual-qa`、`batch-import` 中），
  先預覽、確認後帶 digest 寫入；同名 build 會被拒絕，寫入後讀回驗證。
- 用工具上傳的結果，TestLink 一律記為自動執行；但 TC 詳細頁的 Execution type 是 TC 本身的屬性，
  上傳不會改它。批次匯入完成後可用 `testlink_mark_automated`（在 `maintenance`、`batch-import` 中）
  預覽這批 TC 中仍為 Manual 者，確認後才改成 Automated，寫入後讀回驗證。
- v1.9.0 起 testlink-mcp 的 `execution` toolset 提供 `testlink_link_bug`（把 Redmine bug 關聯到 TestLink TC）。
  需在 testlink-mcp env 檔設定本人的 `TESTLINK_TESTER_ID` 與公司 bug 關聯端點名稱 `TESTLINK_BUG_LINK_ENDPOINT`（向 TestLink 管理者取得，不寫進 repo）；寫入時 `audit_dir` 同樣要給可寫入的絕對路徑。
  預覽要先檢查 `pre_write_checks`，寫入結果只會是 `submitted`／`link_unverified`，關聯須到 TestLink 頁面確認。
- 呼叫 `qa_preview_report_artifact`，取得 operation ID、artifact 路徑與 digest。
- 呼叫 `qa_read_preview_artifact`，使用同一組身分、路徑與 digest。
  預設 `section=items`、`offset=0`、`limit=5`；持續使用回傳的 `next_offset`，
  直到為 null。依 `section_counts` 另讀 `warnings` 與 `ignored`。
- items 保留計畫中的 TestLink request／preview、Redmine request／preview，
  可核對完整安全 payload；每次最多 50 筆，較長內容可改用 `limit=1`。
  這是筆數限制，不是字元截斷，單筆內容不會被靜默截短。
- 此工具只讀取經 digest 驗證的計畫快照，忽略未被該 digest 保護的 review 副本；
  不會重新查詢公司系統，也不代表報表仍未變更。execute 仍重新檢查報表 hash。
- 使用者明確確認環境、target、內容後，才可呼叫 execute 並設 `write=true`。
  建立 Redmine bug 必須另有明確 opt-in；不能只憑摘要或 digest 自行寫入。
- 失敗後沿用同一 operation、preview 與 audit 續跑，不另起整批寫入。
  正式 Redmine 不得因工具缺失改用瀏覽器。

## 5. 離線健檢

新版安裝後執行（請替換路徑）：

```powershell
testlink-agent doctor --server qa `
  --executable "C:/Users/<username>/.local/bin/qa-integration-agent-mcp.exe" `
  --testlink-env-file "C:/Users/<username>/.config/testlink-agent/testlink_mcp.env" `
  --redmine-env-file "C:/Users/<username>/.config/testlink-agent/redmine_mcp.env"
```

加上 `--json` 可取得結構化結果。開發本 checkout 時可使用
`python testlink_agent.py doctor --json`。憑證檔選項傳遞的是路徑，不是憑證。
沒有傳選項時，qa 使用 `QA_TESTLINK_MCP_ENV_FILE`／`QA_REDMINE_MCP_ENV_FILE`；
`--server testlink` 或 `--server redmine` 則只檢查各自的 `*_MCP_ENV_FILE`。

退出碼 0 表示沒有 error；warning 仍需留意，退出碼 1 表示需要修正。
健檢只檢查本次程序環境，不自動載入 Claude/Codex 設定。
若要核對客戶端 toolset，請讓健檢環境的 toolset 變數與 JSON 中一致。

檢查範圍：Python、套件版本、來源是否被 checkout 遮蔽、PATH 執行檔、
工具集合、憑證檔存在與可開啟。檔案內容、密鑰有效性、corp/sandbox 值、
伺服器連線與用戶權限均不在離線健檢範圍。
若客戶端使用絕對路徑，請在 `--executable` 傳入相同的實際路徑。
有指定時只檢查該檔案，不依賴 PATH、不在指定路徑錯誤時退回 PATH。
路徑須為既有檔案且可執行（Windows 接受 .exe/.com/.bat/.cmd）；
沒有指定時才沿用 PATH 查找，找不到仍回傳 error。
健檢不啟動執行檔，也不證明它的內容或套件身分；仍需核對安裝來源。

## 6. 多個 session 並行開發

同時開多個 Claude／Codex session 時，主 checkout（`D:\UseTestlink`）共用一份工作目錄與 index，
一個 session 的未提交改動或 commit 會落到另一個 session 的分支上，推送時被一起帶進 PR。

- 會 commit 的 session 一律在自己的 worktree 工作（例如 `.claude/worktrees/<名稱>`），從 `origin/main` 開分支。
- 主 checkout 只做唯讀查詢、`main` 的更新與啟用設定；不要在這裡切分支開發。
- 更新別人的 PR 分支時，另開 detached worktree 從 `origin/<分支>` 檢出，不要沿用本機同名分支，以免帶入未推送的 commit。
- worktree 只含 git 追蹤的檔案；依賴 `.env`、`local/` 的測試在 worktree 會跳過，需要時改在主 checkout 驗證。
- 合併後用 `git worktree remove <路徑>` 與 `git branch -d <分支>` 清理。

啟用 `git config core.hooksPath .githooks` 後，在主 checkout 的非 `main` 分支 commit 時，pre-commit 會顯示警告但不擋下。

## 官方參考

- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [CLAUDE.md 匯入 AGENTS.md](https://code.claude.com/docs/en/memory)
- [Claude Desktop 本機 MCP](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)
- [遠端 connector 的網路限制](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)

Claude 網頁版的遠端 connector 無法直接使用此本機 stdio 設定；
遠端部署與公司網路開放不屬於本次接入範圍。
