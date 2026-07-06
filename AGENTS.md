# TestLink Agent 操作守則

本 repository 內的 assistant 回覆一律使用繁體中文。

專案/package 名稱：`testlink-agent`。
MCP server 名稱：`testlink-mcp`。
正式專案角色：TestLink + 公司 Redmine/eITS 的 QA 整合層。

本專案不是 TestLink 本體，也不是第二套正式 Redmine。它的責任是把 automation report、TestLink、公司 Redmine/eITS 流程安全地串起來，並保留 preview、schema 驗證、去重、雙向追溯、audit log 與可重跑能力。

## 專案邊界

- TestLink 是測試紀錄系統，負責 project、plan、platform、build、test case 與 execution result。
- 公司 Redmine/eITS 是正式缺陷流程，負責 RM#、eITS#、issue 狀態、assignee、fixed version 與 release note 對應。
- `testlink-agent` 只做整合、驗證、預覽、寫入保護、追溯與 audit。
- 任何自架 Redmine 都只能是 sandbox，不能當成正式缺陷系統。

## 不可破壞的規則

- 所有可寫入操作預設都必須是 preview。
- 只有在使用者明確確認後，才可以使用 `--write` 或 MCP `write: true`。
- 建立 Redmine bug 必須額外 opt-in：`--redmine-create-bugs` 或對等 MCP 參數。
- 破壞性操作必須再次明確確認。
- 不可自行推論 deletion、overwrite、issue closure、assignment 或 fixed version 變更。
- 不可把 sandbox Redmine 視為正式 Redmine/eITS。
- 不可在 log、MCP response、error、audit log 中暴露 devKey、API key、token、password。
- 測試必須能離線執行，不可打到公司 TestLink 或公司 Redmine/eITS。

## 修改前必讀

- 架構或責任邊界：先讀 `docs/architecture.md`。
- 匯入、寫入、重跑與 retest 流程：先讀 `docs/workflow.md`。
- Redmine 欄位、RM#/eITS# 慣例、manager-only 欄位：先讀 `docs/redmine-fields.md`。
- 涉及 write path 時，必須檢查 `testlink_agent_core.policy`、`testlink_agent_core.audit` 與相關 tests。

## 寫入路徑要求

新增或修改任何寫入路徑時，必須同時具備：

- preview-first 行為
- `corp` / `sandbox` profile guard
- Fail/Error 的 Redmine dedupe 與 reuse 規則
- 雙向追溯資訊
- audit JSON
- partial failure 可重跑且具冪等保護
- secret redaction
- 離線單元測試

如果缺少其中一項，不要把寫入能力接上正式流程。

## Redmine/eITS 規則

- 正式 bug 必須使用公司 Redmine/eITS 流程。
- 建單前必須先去重。
- dedupe marker 相同且 open issue 存在時，重用既有 issue。
- 重用 issue 且 TestLink execution 寫入成功後，要補 Redmine evidence comment。
- agent 可以建立 issue description 或留言補證據。
- agent 不可關單、改狀態、改 assignee、改 fixed version。
- `assigned_to_id` 與 `fixed_version_id` 預設是 manager-only 欄位。
- `REDMINE_ALLOW_MANAGER_FIELDS=true` 只能用在 manager-owned machine 或已核准環境。

## TestLink 規則

- TestLink execution notes 若有連到 Redmine，必須包含 Redmine ID/URL。
- Result upload 預設 append execution record。
- `overwrite_result` 與 `delete_execution` 是破壞性操作，必須明確確認。
- TestLink 1.9.16 XML-RPC 行為可能有版本差異；新增版本假設時要補文件與測試。

## Traceability 格式

Release note 的 Redmine 格式：

```text
[Bug #<Redmine ID>] <Ticket Subject>
```

同時存在 RM# 與 eITS# 時，RM# 必須放在 eITS# 前面，讓 PQA 匯入 TestLink 時能自動對應。

TestLink execution notes 應包含：

```text
REDMINE-ID: #<issue id>
REDMINE-URL: <issue url>
REDMINE-REUSED: yes/no
Dedupe Key: testlink-agent:<digest>
```

Redmine description 或 comment 應包含 TestLink project、plan、platform、build、test case、result、report file 與 dedupe marker。

## Report Schema 規則

- 目前支援的 automation report schema 是 `legacy-web-ems-report-v1`。
- parser 必須嚴格驗證 schema。
- 不認得的格式要 fail fast，不可猜測或容錯匯入。
- 新增 schema version 時，必須補 parser 測試與 workflow 文件。

## Security 規則

- 不可 commit `.env`、`local/`、下載的 report、個人 API key 或 devKey。
- 必須遮罩 `TESTLINK_DEVKEY`、`REDMINE_API_KEY`、`password`、`token` 與其他 secret。
- MCP tool 的 structured result、content、error 都要遵守 secret redaction。
- 新增 log、exception 或 audit 欄位時，要先確認不會寫入 secret。

## Definition Of Done

- 相關文件已同步更新。
- 新增或修改的行為有離線測試。
- `python -m unittest discover -s tests` 通過。
- PR 說明有寫清楚 safety impact 與 validation。
- 確認沒有 `.env`、`local/`、API key、devKey 或下載 report 被加入 git。

## 建議操作流程

1. 先讀 `docs/architecture.md`、`docs/workflow.md`、`docs/redmine-fields.md`。
2. 確認目前 profile 是 `corp` 或 `sandbox`。
3. 對可寫入命令先跑 preview。
4. 檢查 target environment、dedupe/reuse decision 與 planned writes。
5. 使用者明確確認後才寫入。
6. 寫入後檢查 `local/audit/` 產生的 JSON 紀錄。
