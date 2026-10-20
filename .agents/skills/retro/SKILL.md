---
name: retro
description: 對一次 coding session 做復盤，找出 agent 工作環境（導航指標、自動檢查、steering 檔、工具 token 成本、資訊取得）的改善候選，依嚴重度提案。僅在使用者要求復盤、retro、檢討 session、找 token 浪費時使用。只讀、只提案，不改程式也不寫外部系統。
disable-model-invocation: true
metadata:
  credits: "改寫自 Matt Pocock 的 retro skill（MIT）：https://github.com/mattpocock/skills/tree/main/skills/engineering/retro"
---

# Retro

目標是改善 agent 的**工作環境**，讓下次 session 更順，而不是修這次的程式。session 卡住、找檔案太久、犯了工具抓得到的錯、拿不到需要的資訊時才值得跑；順利的 session 沒什麼可學。

## 安全邊界

- 全程唯讀。不呼叫任何 `write: true`，不為了驗證而連線公司 TestLink 或 Redmine/eITS，不讀 `.env`、`local/`、report 的內容。
- session 紀錄可能含 devKey、API key、token、password 或客戶資料。引用時只寫時間點與工具名稱，必要片段先遮罩，不得原文轉貼。
- 提案不得弱化 `AGENTS.md` 的「不可退步的安全契約」：preview 預設、digest 失效、明確確認、profile guard、Redmine dedupe/reuse、追溯、audit、resume、secret redaction、離線測試。例如「preview 太花 token，直接寫入」一律不是候選。省 token 的提案必須保留所有 guard。
- 安全契約那一節不列入刪除候選；只能提議替它補上會失敗的自動檢查。
- 只提案。使用者挑選後才實作，實作依一般流程：改檔、跑 `python -m unittest discover -s tests`、用 PR 範本寫 Evidence 與 Merge Danger。

## 步驟

1. 讀使用者指定的 session；未指定就用目前 session。太長已失焦時，請使用者開新 session，指向舊紀錄（Claude Code 在 `~/.claude/projects/<專案>/*.jsonl`，Codex 在 `~/.codex/sessions/`）。
2. 先看既有檢查，避免重造：`tests/`（含 `tests/test_token_budget.py` 的字數上限）、`scripts/token_budget_report.py`、`.github/pull_request_template.md`。已存在但沒接上或壞掉的檢查，發現就是「接上它」。沒有 pre-commit 也沒有 CI 跑測試，本身就是一個發現。
3. 依下表找候選。每個候選都要指得出 session 中的具體時刻；指不出的就刪掉，不要為了填滿分類而寫通用建議。
4. 依嚴重度排序後呈現。安靜但昂貴的錯（例如重複呼叫大量回傳的工具）不要排在吵鬧但便宜的錯後面。

| session 中發生的事 | 修在哪裡 |
|---|---|
| 找某個檔案或事實花很久 | 從 agent 必讀的檔案加導航指標（router `testlink-agent/SKILL.md` 或對應 skill），不是把內容塞進 `AGENTS.md` |
| 犯了工具抓得到的錯 | 自動檢查：離線 unittest、schema/contract 驗證、pre-commit、CI job |
| reviewer 漏掉需要判斷的錯 | `CODING_STANDARDS.md`（尚不存在；第一次有判斷型規則時提議建立，審 PR 時讀） |
| `AGENTS.md`／`CLAUDE.md` 變肥 | 把 steering 移到 skill、docs 或自動檢查；安全契約除外 |
| 工具呼叫回傳過大或重複呼叫 | 精簡 MCP tool schema、加批次工具或 toolset profile，並更新 `docs/token-budget.md` 與預算測試 |
| steering 檔裡有不改變行為的句子 | 刪除這些 no-op |
| 需要的資訊拿不到 | 擴大唯讀存取，例如把 log 寫到檔案、sandbox 唯讀查詢；不得放寬公司系統的寫入權限 |

先分類違規：**機械式**（禁用 API、import 形式、檔案位置、敏感檔被 staged、測試連外）一律做成會失敗的檢查；只有**判斷題**（跨檔一致性、命名是否貼近領域）才寫成文字規則。能寫檢查就不要寫一句話。

## 檔案分工

- `AGENTS.md`／`CLAUDE.md`：每個 session 都會載入，只放導航指標與安全契約。字數上限由 `tests/test_token_budget.py` 守著。
- `.agents/skills/*`：依任務載入的 SOP；新增時在 router 加一行。
- `docs/*`：參考文件，由 skill 或 `AGENTS.md` 指過去；新增前先找既有文件。

## 輸出格式

每個候選一段，依嚴重度排序：

```markdown
### <序號>. <一句話問題>（嚴重度：高／中／低）
- **時刻：** <session 中的時間點或第幾次工具呼叫，不含 secret>
- **分類：** <導航／自動檢查／判斷規則／steering 減肥／工具成本／no-op／資訊取得>
- **提案：** <要改哪個檔、加什麼檢查>
- **安全影響：** <不影響安全契約，或說明如何維持>
- **成本：** <大約要改多少、會不會增加每個 session 的 token>
```

最後請使用者選要做哪幾項；沒選的不動。
