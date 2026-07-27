---
name: testlink-testcase-maintenance
description: Use for creating or updating TestLink testcases with protected row policy, preview digest, and readback verification.
---

# Testcase Maintenance

使用 `testlink-mcp` 的 `maintenance` toolset。執行前完整閱讀 `references/protected-write.md`，依其單列 step policy、preview、明確確認、readback 與 verification report 操作。

只使用 `testlink_create_testcase` 或 `testlink_update_testcase`；不得以 legacy alias、CLI 或 multi-row 參數繞過保護契約。
