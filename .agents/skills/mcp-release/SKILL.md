---
name: mcp-release
description: Use for building, validating, installing, upgrading, or rolling back tagged testlink-agent MCP releases.
---

# MCP Release

完整依 `docs/deployment.md` 操作。`D:\UseTestlink` 只供開發；其他專案須從已審查 GitHub tag 安裝 user-level 隔離工具，不得依賴此 checkout、editable install 或 repository `cwd`。

發布前跑完整離線測試、build wheel、在乾淨環境驗證三個正式 entrypoint，並檢查敏感檔案未 staged。安裝後確認 executable 與 package source 不在開發 checkout。credential env file 必須留在本機並與 package 分離；live smoke test 只可讀。
