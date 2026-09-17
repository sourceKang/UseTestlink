# Claude 接入與離線健檢

本專案透過本機 stdio MCP 供 Claude Code 與 Claude Desktop 使用，
沿用相同的 TestLink／Redmine／QA 責任邊界。

## 版本與驗證範圍

`doctor` 與 `qa_read_preview_artifact` 是本次尚未發布的開發變更，
既有 v1.7.0 tag 不包含這些新增功能。待變更審查並發布新版後，
共用環境才依 `deployment.md` 安裝該 tag。不要將其他專案指向開發 checkout。
離線測試與乾淨 wheel 安裝驗證不代表已完成 Claude 應用程式內的端到端驗證。

## 1. 準備本機設定

1. 依 `deployment.md` 安裝經審查的發布版本。
2. 用 `Get-Command qa-integration-agent-mcp | Select-Object Source` 找到實際執行檔。
3. 保留既有、各自獨立的 TestLink 與 Redmine 憑證檔；使用絕對路徑。
   範本中的 `.config/testlink-agent` 只是建議位置，不必搬移現有檔案。
4. 把範本的 `<username>`、執行檔與憑證檔位置換成實際值。
   憑證本身不得寫進 JSON、提示詞或工具參數。

範本只有一個 `qa-integration-agent`，工具集合為 `import`。
單獨維護 testcase 或查詢 Redmine 時，依 README 的直接操作流程切換服務，
不要預設同時註冊所有服務。

## 2. Claude Code

將 `claude-code-mcp.example.json` 的 `mcpServers` 項目合併到使用專案的
`.mcp.json`，保留既有其他服務，不要整份覆蓋。也可依官方文件採用個人範圍註冊。
使用者機器的實際路徑不應提交到共用 repository；此 repository 已忽略 `.mcp.json`。
啟動 Claude Code 後，用 `/mcp` 查看服務狀態與工具清單。

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
  `artifact_dir` 也建議指定可寫入的本機絕對路徑，避免 GUI 工作目錄差異。
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
PATH 中的執行檔與客戶端指定的絕對路徑可能不同，需核對後再使用。

## 官方參考

- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [CLAUDE.md 匯入 AGENTS.md](https://code.claude.com/docs/en/memory)
- [Claude Desktop 本機 MCP](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)
- [遠端 connector 的網路限制](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)

Claude 網頁版的遠端 connector 無法直接使用此本機 stdio 設定；
遠端部署與公司網路開放不屬於本次接入範圍。
