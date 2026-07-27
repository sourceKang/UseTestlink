---
name: testlink-agent
description: Route work in the testlink-agent repository to the smallest TestLink, Redmine/eITS, QA import, testcase maintenance, or MCP release workflow.
---

# TestLink Agent Router

只載入本次任務對應的 skill，不要預先讀取全部 SOP：

- automation report 跨系統匯入：`qa-report-import`
- TestLink execution／target discovery：`testlink-execution`
- testcase 建立或更新：`testlink-testcase-maintenance`
- Redmine issue、metadata、evidence comment：`redmine-issue`
- MCP package 發布、安裝、rollback：`mcp-release`

若只修改純程式且不碰上述流程，遵守 `AGENTS.md` 與相關測試即可。

所有外部寫入仍須 preview、未變更的 `preview_digest`、明確確認與 audit；不得在工具參數中傳遞憑證。整合匯入優先只註冊 `qa-integration-agent`；直接操作上游時才切換到相應 MCP profile。
