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
Claude Code／Desktop 應用程式內端到端驗證尚未進行。
