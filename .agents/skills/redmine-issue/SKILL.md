---
name: redmine-issue
description: Use for company Redmine/eITS metadata, template validation, bug preview/create, dedupe, or evidence comments.
---

# Redmine Issue

使用 `redmine-mcp` 的 `metadata` 或 `issue` toolset，並先讀 `docs/redmine-fields.md`。

1. 確認 `corp`／`sandbox`、project、template 與 operation ID。
2. 建單前先去重；open match 重用，closed match 阻擋自動重開。
3. 先呼叫 preview。公司 template 中 Severity 由內建 `priority_id` mapping 解析；自訂 Priority 是獨立 custom field，不得共用 mapping 或猜 ID。
4. 摘要需分列 Severity、custom Priority、dedupe 決策、attachments、warnings、artifact 與 digest。
5. 僅在明確確認後，用未變更輸入及 digest 寫入；寫後讀回驗證欄位並回報 audit ID。

只可建立描述或補 evidence comment，不可關單、改狀態、assignee 或 fixed version。正式 Redmine MCP 不可用瀏覽器替代。
