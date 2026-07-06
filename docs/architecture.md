# TestLink Agent 架構

`testlink-agent` 提供 `testlink-mcp` server，讓 agent 能安全操作 TestLink，並受控地整合公司 Redmine/eITS 流程。

本專案是 QA 整合層。它不取代 TestLink，也不建立第二套正式 Redmine 流程。

## 系統角色

```text
Automation Report
        |
        v
testlink-agent / testlink-mcp
        |
        +--> TestLink
        |
        +--> Corporate Redmine / eITS
```

## 資料權責

| 領域 | Source of truth | 說明 |
|---|---|---|
| Test project、test plan、platform、build | TestLink | `testlink-agent` 透過 XML-RPC 讀寫。 |
| Test case 與 execution result | TestLink | Execution notes 可以包含 Redmine 追溯資訊。 |
| Bug、RM#、eITS#、assignment、status、fixed version | Corporate Redmine/eITS | 正式缺陷必須使用公司系統。 |
| Automation report 匯入決策 | `testlink-agent` preview + 使用者確認 | 寫入操作必須 opt-in。 |
| 操作證據 | `local/audit/*.json` | 本機 audit 檔案由 git ignore。 |

## 部署邊界

TestLink 與 Redmine/eITS 必須分開：

- 分開的 service
- 分開的 database
- 分開的 backup 與 restore 流程
- 分開的使用者角色與權限
- 只透過 API 整合

如果之後部署本機 Redmine，必須明確命名並文件化為 sandbox。它不能用於正式 RM#、eITS#、release note 或 PQA import 流程。

選用的 `infra/redmine-sandbox/` compose 設定只供開發使用。它不是 production Redmine recipe，也不能接到正式 TestLink/PQA release-note 流程。

## 環境 Profile

每一次可寫入操作都必須知道目標環境：

```text
TESTLINK_AGENT_PROFILE=corp
REDMINE_ENV=corp
```

或：

```text
TESTLINK_AGENT_PROFILE=sandbox
REDMINE_ENV=sandbox
```

`corp` 代表公司正式 TestLink 與公司 Redmine/eITS 流程。`sandbox` 代表本機或開發專用系統。agent 不可推論 sandbox Redmine 是正式系統。

## 寫入安全模型

所有寫入路徑都遵守這個模型：

```text
parse input
  -> validate schema and target profile
  -> resolve TestLink target
  -> compute dedupe key for Fail/Error results
  -> preview TestLink and Redmine actions
  -> wait for explicit confirmation
  -> write
  -> record audit log
```

可寫入命令預設都是 preview。Redmine bug creation 也必須 opt-in。破壞性操作需要額外確認。

## 主要模組

| 模組 | 責任 |
|---|---|
| `testlink_agent_core.cli` | CLI 參數與 command routing。 |
| `testlink_agent_core.commands` | CLI command orchestration。 |
| `testlink_agent_core.mcp_server` | MCP server entrypoint 與 tool exposure。 |
| `testlink_agent_core.client` / `clients` | TestLink XML-RPC 存取。 |
| `testlink_agent_core.redmine` | Redmine API 存取與 issue payload 建構。 |
| `testlink_agent_core.reports` | Automation report parsing。 |
| `testlink_agent_core.policy` | 目標環境、允許欄位、去重與冪等規則。 |
| `testlink_agent_core.audit` | 寫入 audit record 與 retry evidence。 |
| `testlink_agent_core.output` | Console/MCP output 與敏感資訊遮罩。 |

`policy.py` 與 `audit.py` 是目前有效的安全模組。任何新的寫入行為都必須重用它們，不要另外實作一次性的 profile、dedupe、audit 或 retry 邏輯。

## 未來結構

不要只為了美觀移動模組。等 policy 與 audit 行為穩定後，core 可以再拆成：

```text
testlink_agent_core/
  integrations/
    testlink_client.py
    redmine_client.py
  reports/
    parser.py
    schema.py
  policy.py
  audit.py
```

這個 migration 必須保持 CLI 與 MCP 行為不變，並維持離線測試通過。

## 非目標

- 不把正式 Redmine 資料存成 TestLink-only 檔案。
- 不把本機 Redmine 當正式缺陷系統。
- 不自動關閉 Redmine issue。
- 不自動指派 issue 或設定 fixed version，除非 manager-owned environment 明確允許。
- 不在 log 或 MCP response 暴露 API key、devKey、token 或 password。
