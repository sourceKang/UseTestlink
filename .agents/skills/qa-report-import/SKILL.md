---
name: qa-report-import
description: Use for previewing, executing, auditing, or resuming automation report imports across TestLink and company Redmine/eITS.
---

# QA Report Import

只使用 `qa-integration-agent` 的 `import` toolset。先讀 `docs/workflow.md`；變更責任邊界時再讀 `docs/architecture.md`。

1. 取得明確的 environment、project、plan、platform、build、report 與 operation ID；不可猜測 target。同一批 testcase 在多個 node 執行時，改傳 `reports: [{label, path}]`（每個 node 一份，label 唯一，與 `report` 二擇一）；每份 report 必須含相同 testcase，任一 Fail/Error 即 f，合併規則見 `docs/workflow.md`。
2. 呼叫 `qa_preview_report_artifact`。摘要顯示 target、counts、Fail/Error、skip、Redmine create/reuse、warnings、`preview_artifact` 與 `preview_digest`（多節點時另列各 report 的 label 與 node）；逐筆 payload 留在 artifact 的 `review`，不要重貼到對話。
3. 使用者審閱 artifact 並明確確認後，以 operation ID、artifact path、digest 與 `write=true` 呼叫 `qa_execute_preview_artifact`，不重傳整份計畫。
4. 驗證 audit 與 traceability；若另有 testlink-mcp，可用 `testlink_get_latest_results`（`expected_status`）一次確認 TestLink 寫入結果；partial failure 以同一 preview artifact、operation identity 和 audit 呼叫 `qa_resume_preview_artifact`，不可另起整批寫入。

Redmine 建單必須 `redmine_create_bugs=true`。要讓 Fail 項目的 bug 同時關聯到 TestLink TC，預覽時另加 `testlink_bug_link=true`（預設關閉）：該 TC 改由 `testlink_link_bug` 寫入一筆含結果與關聯的 execution，預覽與摘要須轉述 `testlink_mode` 與副作用（同 build 第一次關聯會在 Redmine 自動留言、伺服器不確認關聯），結果只能說「已送出」。未知 report schema 要 fail fast；正式流程不得改用 sandbox 或瀏覽器。舊 `qa_preview_report_import`／`qa_execute_report_import`／`qa_resume_report_import` 僅供 `legacy` toolset 相容，不是新流程首選。
