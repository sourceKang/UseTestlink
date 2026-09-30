# QA MCP Contracts

本目錄保存 `testlink-mcp`、`redmine-mcp` 與 `qa-integration-agent` 之間的版本化資料契約。

共用的 canonical JSON、payload digest、preview digest 驗證與 secret-bearing field guard 位於 `qa_mcp_contracts/`；拆分 repository 時再獨立發布為小型版本化 package。

## 規則

- `v1/`、`v2/` 內的 schema 使用 JSON Schema Draft 2020-12。
- 所有 structured result 都必須包含 `schema_version` 與 `operation_id`。
- preview contract 必須包含 `mode: preview`、`preview_digest` 與 `planned_write`。
- write 必須重新計算 payload digest，且只接受與已確認 preview 相同的 `preview_digest`。
- contract 不得包含 devKey、API key、password、token、Authorization header 或其他 secret。
- 不相容欄位變更必須建立新的 major contract 目錄，不得直接修改既有語意。

## v1 schemas

- `operation-context.schema.json`
- `error.schema.json`
- `testlink-execution-preview.schema.json`
- `testlink-execution-result.schema.json`
- `redmine-bug-preview.schema.json`
- `redmine-bug-result.schema.json`
- `redmine-comment-preview.schema.json`
- `redmine-comment-result.schema.json`
- `qa-report-preview.schema.json`
- `workflow-audit.schema.json`

## v2 schemas

`qa_preview_report_artifact` 以 `reports: [{label, path}]`（每個 node 一份 report）建立的 plan 使用 v2；以單一 `report` 建立的 plan 維持 v1，digest 不變。v2 只新增下列兩份 schema，其餘 child contract 仍為 v1。

- `qa-report-preview.schema.json`：`schema_version: "2.0"`；以 `reports`（label、path、report_file、sha256、report_schema、header 子集）取代單一 `report_schema`；`input_digest` 是依 `reports` 順序對每份 report 的 `{label, sha256}` 計算的 canonical digest；每個 item 另帶 `nodes`（各 node 的 raw status 與 duration）。
- `workflow-audit.schema.json`：`schema_version: "2.0"`；`workflow.reports` 記錄每份 report 的 label、檔名、sha256 與 schema，resume 時必須完全相同。
