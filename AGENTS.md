# TestLink Agent 全域守則

本 repository 的 assistant 一律使用繁體中文。專案名稱為 `testlink-agent`，正式邊界為 `testlink-mcp`、`redmine-mcp`、`qa-integration-agent`；`testlink-agent-mcp` 只供遷移相容。

## 責任邊界

- TestLink 保存測試案例與 execution；公司 Redmine/eITS 保存正式缺陷。
- 本專案只負責整合、驗證、preview、追溯、audit 與 resume；自架 Redmine 只能是 sandbox。
- TestLink-only、Redmine-only、跨系統流程分別由上述三個正式 MCP 負責，且維持獨立認證與 failure domain。

## 不可退步的安全契約

- 所有外部寫入預設 preview；payload 變更後舊 `preview_digest` 立即失效。
- 僅在使用者明確確認環境、target、內容後才可 `write: true`；建立 Redmine bug 還需明確 opt-in。
- 禁止猜測 target、數字 ID、刪除、overwrite、關單、狀態、assignee 或 fixed version；manager-only 欄位須核准環境。
- 正式 Redmine 不可因 MCP 缺失改用瀏覽器；應回報缺少的 server 或 credential path。
- 寫入路徑必須保有 profile guard、Redmine dedupe/reuse、雙向追溯、audit JSON、冪等 resume、secret redaction 與離線測試。
- 不得在參數、log、response、error、audit 或 git 中暴露／提交 `.env`、`local/`、report、API key、devKey、token、password。
- 測試不得連線公司 TestLink 或 Redmine/eITS。

## 按任務載入文件

先用 `.agents/skills/testlink-agent/SKILL.md` 路由，只讀本次任務需要的 skill：報表匯入 `qa-report-import`；TestLink execution `testlink-execution`；testcase 維護 `testlink-testcase-maintenance`；Redmine `redmine-issue`；發布安裝 `mcp-release`。

架構邊界讀 `docs/architecture.md`；write/resume 讀 `docs/workflow.md`；Redmine 欄位讀 `docs/redmine-fields.md`。不要為單一任務載入所有文件。

## 完成條件

同步必要文件與離線測試，執行 `python -m unittest discover -s tests`，說明 safety impact 與 validation，並確認敏感檔案未加入 git。
