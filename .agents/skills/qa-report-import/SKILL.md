---
name: qa-report-import
description: Use for previewing, executing, auditing, or resuming automation report imports across TestLink and company Redmine/eITS.
---

# QA Report Import

只使用 `qa-integration-agent` 的 `import` toolset。先讀 `docs/workflow.md`；變更責任邊界時再讀 `docs/architecture.md`。

1. 取得明確的 environment、project、plan、platform、build、report 與 operation ID；不可猜測 target。
2. 呼叫 `qa_preview_report_artifact`。摘要顯示 target、counts、Fail/Error、skip、Redmine create/reuse、warnings、`preview_artifact` 與 `preview_digest`；逐筆 payload 留在 artifact 的 `review`，不要重貼到對話。
3. 使用者審閱 artifact 並明確確認後，以 operation ID、artifact path、digest 與 `write=true` 呼叫 `qa_execute_preview_artifact`，不重傳整份計畫。
4. 驗證 audit 與 traceability；partial failure 以同一 preview artifact、operation identity 和 audit 呼叫 `qa_resume_preview_artifact`，不可另起整批寫入。

Redmine 建單必須 `redmine_create_bugs=true`。未知 report schema 要 fail fast；正式流程不得改用 sandbox 或瀏覽器。舊 `qa_preview_report_import`／`qa_execute_report_import`／`qa_resume_report_import` 僅供 `legacy` toolset 相容，不是新流程首選。
