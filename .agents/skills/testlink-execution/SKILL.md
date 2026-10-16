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

確認多筆最新結果時用 `testlink_get_latest_results`（同一個精確 build/platform，搭配 `expected_status` 只列出不符合者），不要逐筆呼叫 `get_last_result`。

建 build 用 `testlink_create_build`：先以精確 project、plan、build 名稱預覽，`duplicate_found` 為 true 就停止；使用者確認後以相同輸入與 digest 寫入，結果須為 `verified`。回 `BUILD_WRITE_INDETERMINATE` 時不可重試，請使用者到 TestLink 頁面確認。

## 關聯 Redmine bug（`testlink_link_bug`）

需要 testlink-mcp env 檔設定 `TESTLINK_TESTER_ID`（使用者本人的 TestLink user ID，無預設值）；未設定會回 `CONFIG_MISSING`，請使用者自行補上，不要讀取或修改 env 檔，也不要改用其他帳號。

1. 用精確的 project、plan、platform、build、testcase external ID（不可帶 `_數字` 後綴）、`bug_ids`、`status`、`notes` 產生預覽。
2. 寫入前先用 redmine-mcp `redmine_get_issue`（唯讀）查每張 bug 的狀態。若是 Resolved／Closed，要特別告知使用者：同一 build 第一次關聯時，仍會被自動留言「The bug is still exist on <project> <build>, please double check the issue」，RD 可能誤以為修好的 bug 還在。
3. 向使用者完整列出 target、TC（external ID、名稱、version_id）、bug IDs 與狀態、會一併寫入的 status、tester_id、notes、`execution_duration_minutes`（預設 5 分鐘）、endpoint，以及三項副作用：一定會新增一筆 execution；同一 build 第一次關聯時 Redmine 會被自動留言；伺服器不確認關聯。status 不是 `f`，或 TC 名稱空白時，要轉述警告。
4. 使用者明確確認後，以相同輸入、`write=true`、未變更的 `preview_digest` 寫入一次。
5. 結果是 `submitted`／`link_unverified`：只能說「已送出關聯請求」，請使用者到 TestLink 頁面確認，不可宣稱關聯成功。readback warning 要轉述。
6. 回 `LINK_BUG_INDETERMINATE` 時不可重試，請使用者先到 TestLink 頁面確認；確認缺少關聯後才用新的 `operation_id` 重新預覽。

此工具不關單、不改 Redmine 狀態、assignee 或版本。細節見 `docs/testlink-mcp-v2.md` 的 Protected Bug Linking。
