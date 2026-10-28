---
name: qa-report-import
description: Use for previewing, executing, auditing, or resuming automation report imports across TestLink and company Redmine/eITS.
---

# QA Report Import

只使用 `qa-integration-agent` 的 `import` toolset。先讀 `docs/workflow.md`；變更責任邊界時再讀 `docs/architecture.md`。

1. 取得明確的 environment、project、plan、platform、build、report 與 operation ID；不可猜測 target。同一批 testcase 在多個 node 執行時，改傳 `reports: [{label, path}]`（每個 node 一份，label 唯一，與 `report` 二擇一）；每份 report 必須含相同 testcase，任一 Fail/Error 即 f，合併規則見 `docs/workflow.md`。
2. 呼叫 `qa_preview_report_artifact`。報表的 `Completeness` 為 Incomplete／not evaluated 或有 `Case ID Drift` 時，預覽回 `REPORT_NOT_READY`：向使用者轉述被擋下的 report 與原因，建議重跑或修正；只有使用者明確接受風險並給出原因，才可帶 `readiness_override_reason` 重新預覽，摘要須轉述 `readiness_override`（規則見 `docs/workflow.md` Report Readiness Gate）。沒有 Completeness 行只是 warning。摘要顯示 target、counts、Fail/Error、skip、Redmine create/reuse、warnings、`preview_artifact` 與 `preview_digest`（多節點時另列各 report 的 label 與 node）；逐筆 payload 留在 artifact 的 `review`，不要重貼到對話。
3. 使用者審閱 artifact 並明確確認後，以 operation ID、artifact path、digest 與 `write=true` 呼叫 `qa_execute_preview_artifact`，不重傳整份計畫。
4. 驗證 audit 與 traceability；若另有 testlink-mcp，可用 `testlink_get_latest_results`（`expected_status`）一次確認 TestLink 寫入結果；partial failure 以同一 preview artifact、operation identity 和 audit 呼叫 `qa_resume_preview_artifact`，不可另起整批寫入。
5. 整批寫入成功後，若有 testlink-mcp 的 `maintenance`（`batch-import`），以同一 project/plan/platform 與這批 TC 呼叫 `testlink_mark_automated` 預覽（新 operation ID）。`to_mark` 為空就不提；否則列出這些 Manual TC（編號、版本），詢問是否改成 Automated：使用者可全部、部分或跳過，部分時只帶選定的 TC 重新預覽。明確確認後才帶 digest `write=true`；這會修改 TC 本身的屬性（TC 修改人／時間改為上傳者），不影響已寫入的結果。結果上傳本身已由 TestLink 記為自動執行，這步只修正 TC 詳細頁的 Execution type。

Redmine 建單必須 `redmine_create_bugs=true`。要讓 Fail 項目的 bug 同時關聯到 TestLink TC，預覽時另加 `testlink_bug_link=true`（預設關閉）：該 TC 改由 `testlink_link_bug` 寫入一筆含結果與關聯的 execution，預覽與摘要須轉述 `testlink_mode` 與副作用（同 build 第一次關聯會在 Redmine 自動留言、伺服器不確認關聯），結果只能說「已送出」。未知 report schema 要 fail fast；正式流程不得改用 sandbox 或瀏覽器。舊 `qa_preview_report_import`／`qa_execute_report_import`／`qa_resume_report_import` 僅供 `legacy` toolset 相容，不是新流程首選。
