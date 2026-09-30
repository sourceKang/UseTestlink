# 第一批易用性改善計畫

日期：2026-09-17。範圍：Claude 接入、離線健檢、完整預覽分頁閱讀。

## 目標與驗收

1. Claude Code 與 Desktop 共用既有 MCP：提供不含憑證的獨立設定範本、
   `CLAUDE.md` 共用規則入口、繁體中文接入說明。預設只啟用整合服務。
2. `testlink-agent doctor`：離線檢查 Python、套件／執行檔、toolset、
   憑證檔位置與可讀性；不讀取憑證內容、不連線、不自動修復。
   缺少設定時回傳非零狀態與可操作的修正提示。
3. `qa_read_preview_artifact`：以 operation ID、artifact 路徑和 digest
   讀取經驗證計畫的分頁內容；可查看每筆 payload、警告與忽略項目。
   每頁重新驗證 digest，拒絕竄改、錯誤身分與無效分頁參數，回應保留遮蔽。
4. 驗證：完整離線 unittest、真實 stdio 子程序握手與工具探索、
   建置 wheel 並在乾淨環境安裝／啟動；檢查 Git 敏感檔案隔離。

## 執行順序

先實作分頁閱讀與健檢，再同步 Claude 範本、工作流程與 README，
最後執行回歸測試與乾淨安裝驗證並記錄結果。

## 安全邊界

不新增外部寫入路徑、不更動既有確認與 resume 條件。
Redmine 建單仍需 opt-in；preview digest 不代表人類授權。
不讀取公司憑證內容，不執行公司系統 smoke test。
不修改使用者現有 Claude/Codex 設定、不發布或推送版本。

## 後續批次

MCP 協定協商完整化、CI、自動化大量匯入效能量測與額外報表格式，
不屬於本次交付。Claude 應用程式內的端到端驗證須另記實測狀態，
不可把離線協定測試當成已完成 Claude 實機驗證。

## 驗證結果

第一批目標已完成（2026-09-17）：

- 已新增 Claude Code／Desktop JSON 範本、CLAUDE.md 共用指令與繁體中文指南。
  已解析驗證兩份範本；只有整合服務、import 工具集合與憑證檔路徑占位符。
- 已新增 `testlink-agent doctor`，提供可讀提示與 JSON，缺漏設定回傳退出碼 1。
- 已新增 `qa_read_preview_artifact`，可分頁讀取完整計畫項目、警告及忽略項目。
- Python 3.14 與 Python 3.12 的完整離線測試各 266 項通過（原有 249 項）。
  新測試涵蓋分頁完整性、錯誤參數、身分／digest 不符、跨頁竄改、
  review 副本不被信任、敏感資訊遮蔽、健檢不讀取憑證及不連線。
- 三個 MCP 均以真實子程序驗證初始化、工具探索、ping；QA 額外驗證
  中文分頁回應與未帶 write 確認時拒絕執行。
  TestLink／Redmine 的上游啟動健檢使用 mock；子程序 socket 被封鎖，
  這不是公司連線或認證有效性測試。
- 使用現有 Python 3.12 建置工具，以 `--no-index --no-deps --no-build-isolation`
  離線建立 wheel；在 checkout 外的新暫存 venv 安裝成功。
  四個 CLI／正式 MCP console entrypoint 檔案均存在；實際執行 doctor
  的成功／失敗路徑，並從已安裝套件重跑三個 MCP 子程序測試成功。
- wheel 內容不含 local、report、下載與憑證檔；Git 暫存區無檔案，
  敏感本機目錄未被追蹤，`git diff --check` 通過。
- QA import 工具 schema 估算 1,321 tokens，相較全部工具減少 88.4%，
  仍符合至少減少 70% 的預算門檻。

驗證用 wheel 位於被忽略的 `local/validation/usability/wheels/`。
套件版本未提升，該 wheel 是本機開發快照，不是已發布的 v1.7.0 tag 成品。
本次未改動使用者的 Claude/Codex 設定、未發布、未執行任何公司系統寫入。
依使用者提供的 Claude Cowork 完整交接報告，commit `a6e7c01` 已在
Linux x86_64／Python 3.11.15、Claude Code 2.1.274 的非互動模式驗證 MCP 接入、
合成 artifact 分頁、繁體中文及拒絕情境；來源與安裝版各 266 項測試通過。
隔離安裝驗證從 checkout 外執行，已確認套件來自 site-packages。
該報告也揭露二次遮蔽破壞內層 JSON 的缺陷，因此上述實測不代表該缺陷已通過。
Windows Claude Code、Claude Desktop、真實 preview 產生及外部寫入仍未實測；
後續修正的應用程式實測狀態不可沿用舊 commit 宣稱完成。

## PR #9 交接修正計畫（2026-09-17）

1. 高優先：四個 MCP server 在結構化資料層完成遮蔽，傳輸層不再對
   已序列化的 JSON 字串套用遮蔽；逐一檢查正常、錯誤及初始化回應。
   收緊共用與 Redmine 字串遮蔽規則，保留 JSON 分隔符及引號。
2. 中優先：doctor 新增 `--executable` 絕對路徑驗證，明確區別 PATH 與指定路徑，
   不啟動待測執行檔、不以 PATH 掩蓋錯誤的指定路徑。
3. 文件：補上 Claude 專案信任與單一 MCP 核准說明，更新 Linux 實測的來源與範圍。
4. 驗收：先重現缺陷，再跑四個 server 的正常／錯誤回應、兩種 framing、
   JSON 可解析及秘密不外洩回歸；補 doctor 路徑案例、完整離線 unittest，
   並以乾淨 wheel 安裝驗證修正版。確認 Git 敏感檔案隔離後更新同一 PR。

本批不合併、不發布、不進行公司系統連線或寫入；Linux 舊版已驗證項目沿用
交接紀錄，修正版的離線回歸不等同 Claude 應用程式重新驗證。

### 本批修正驗收結果

- 修正前新增回歸可重現：四個 server 的正常／失敗回應內層 JSON 解析失敗。
- 修正後來源測試：Windows Python 3.14 與 3.12 各 275 項通過。
- 四個 server 的 line／Content-Length 回應均可解析外層與內層 JSON，
  TESTLINK_DEVKEY、REDMINE_API_KEY、devKey 與結構化秘密欄位仍被遮蔽。
  錯誤 data、未知 method、例外、request ID 與初始化回傳值維持遮蔽。
- line framing 固定輸出 UTF-8 bytes；非 UTF-8 stdout 文字編碼的回歸通過。
  這不代表公司 TestLink 的既有中文資料編碼問題已查明。
- doctor 指定路徑時不使用 PATH、不啟動程式，無效路徑不會回退至 PATH；
  相對路徑、目錄、不存在及不可執行檔案會報錯。
- 修正版 wheel 已離線建置，於 checkout 外新 venv 非 editable 安裝。
  五個套件確認載入自該 venv 的 site-packages 後，完整 275 項測試通過；
  實際 console doctor 在空 PATH、指定隔離環境執行檔時成功。
- `.claude/settings.local.json` 與 `.mcp.json` 均被 Git 忽略；
  變更僅含程式、合成測試與文件，沒有交接報告原始資料、憑證或公司資料。

驗證用 wheel 留在 `local/validation/pr9-fixes/wheels/`，不是正式發布。
修正版 Claude 應用程式實測仍待後續；本批沒有真實系統連線或寫入。
