---
name: testlink-execution
description: Use for exact TestLink target discovery and previewing or appending protected execution results.
---

# TestLink Execution

使用 `testlink-mcp` 的 `execution` toolset，涉及寫入時讀 `docs/workflow.md`。

1. 以 `testlink_resolve_execution_target` 一次解析明確 project、plan、platform、build 與 testcase external ID；零筆或多筆都停止，不猜測最接近名稱。
2. 用 `testlink_report_execution` 產生 preview，檢查 target、status、notes、Redmine trace、operation ID 與 digest。
3. 使用者確認後，以完全相同輸入、digest、`write=true` append execution。
4. 要求 readback／audit 證據；若驗證不明，不可宣稱成功或重複寫入。

若 notes 連到 Redmine，必須含 issue ID/URL、reuse 狀態與 dedupe key。overwrite／delete 必須另行明確確認，不屬一般流程。
