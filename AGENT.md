# TestLink Agent CLI 操作指引

你正在透過 `testlink_agent.py` 操作 TestLink。回覆使用者時，必要內容請使用繁體中文，並保持精簡。

## 憑證與本機資料

- TestLink URL 讀取 `TESTLINK_URL`。
- TestLink API key 讀取 `TESTLINK_DEVKEY`。
- 建立 testcase 時，author login 讀取 `TESTLINK_AUTHOR_LOGIN`。
- 未提供 `--env-file` 時，依序讀取 `TESTLINK_AGENT_ENV_FILE` 指向檔案、`.env`、`local/testlink_agent.env`。
- 建立 Redmine bug 時讀取 `REDMINE_URL` 與 `REDMINE_API_KEY`；使用 `--redmine-issue-id` 時若未提供 `--redmine-issue-url`，會用 `REDMINE_URL` 組出 issue URL。
- 不要印出、提交或要求使用者貼出任何 key；對話中一律遮蔽為 `*****`。
- 個人執行資料保留在 `.env`、`local/`、`downloads/`、`reports/`、`output/` 或 `outputs/`，不要提交。
- 下載的 testcase JSON、Excel 與 automation report 預設只供本機使用；除非使用者明確要求，否則不要上傳或提交。

## 操作原則

1. project、plan、platform、build 或 suite 不清楚時，先用唯讀查詢確認。
2. 缺少 key 時，先檢查 `.env`、`local/testlink_agent.env` 或 `TESTLINK_AGENT_ENV_FILE`。
3. 所有寫入操作前，一律先 preview。
4. preview 後確認 project、test plan、platform、build 與結果筆數。
5. 只有 preview 沒有 missing 或 duplicate testcase 時，才加上 `--write`。
6. 使用 `--redmine-create-bugs` 時，寫入前先確認 Redmine issue preview。
7. 完成後回報成功與失敗筆數。

建立 testcase 前，必須確認 project、test suite ID 或 suite name、testcase name、author login、duplicate action 與 steps。

## 常用指令

```powershell
# 列出 projects
python .\testlink_agent.py list-projects

# 列出 plans
python .\testlink_agent.py list-plans --project "YourProject"

# 列出 platforms
python .\testlink_agent.py list-platforms --project "YourProject" --plan "Your Test Plan"

# 列出 active/open builds
python .\testlink_agent.py list-builds --project "YourProject" --plan "Your Test Plan" --open-only

# 列出 test suites
python .\testlink_agent.py list-suites --project "YourProject"

# 更新 catalog 並搜尋 suites
python .\testlink_agent.py refresh-catalog
python .\testlink_agent.py find-suites --project-contains "Gateway" --suite-contains "VPN"

# 下載 test cases
python .\testlink_agent.py download-testcases --project "YourProject" --plan "Your Test Plan" --platform "Your Platform" --out testcases.json
python .\testlink_agent.py download-testcases --project "YourProject" --plan "Your Test Plan" --platform "Your Platform" --format xlsx --out testcases.xlsx

# 預覽建立 testcase
python .\testlink_agent.py create-testcase --project "YourProject" --suite-name "Test_Case_Group" --name "can_login" --summary "Verify that a valid user can log in." --step "Open the login page => Login form is shown" --step "Submit valid credentials => Dashboard is shown" --importance high --execution-type automated

# 確認 preview 後建立 testcase
python .\testlink_agent.py create-testcase --project "YourProject" --suite-name "Test_Case_Group" --name "can_login" --summary "Verify that a valid user can log in." --step "Open the login page => Login form is shown" --step "Submit valid credentials => Dashboard is shown" --importance high --execution-type automated --write

# 預覽 / 寫入 report upload
python .\testlink_agent.py upload-report --project "YourProject" --plan "Your Test Plan" --platform "Your Platform" --report "C:\path\to\report.txt" --skip-policy ignore
python .\testlink_agent.py upload-report --project "YourProject" --plan "Your Test Plan" --platform "Your Platform" --report "C:\path\to\report.txt" --skip-policy ignore --write

# 預覽 / 寫入 Redmine bug
python .\testlink_agent.py upload-report --project "YourProject" --plan "Your Test Plan" --platform "Your Platform" --report "C:\path\to\report.txt" --skip-policy ignore --redmine-create-bugs
python .\testlink_agent.py upload-report --project "YourProject" --plan "Your Test Plan" --platform "Your Platform" --report "C:\path\to\report.txt" --skip-policy ignore --redmine-create-bugs --write

# 預覽記錄既有 Redmine issue
python .\testlink_agent.py upload-report --project "YourProject" --plan "Your Test Plan" --platform "Your Platform" --report "C:\path\to\report.txt" --skip-policy ignore --redmine-issue-id 255162 --redmine-issue-url "https://redmine.example.com/issues/255162"

# 確認 preview 後，用既有 Redmine issue 寫回 TestLink execution result 與 note
python .\testlink_agent.py upload-report --project "YourProject" --plan "Your Test Plan" --platform "Your Platform" --report "C:\path\to\report.txt" --skip-policy ignore --redmine-issue-id 255162 --redmine-issue-url "https://redmine.example.com/issues/255162" --write
```

未提供 `--build` 或 `--build-id` 時，CLI 會選擇最新 active/open build，並在 preview 顯示選到的 build。需要指定 release 時，優先使用 `--build "Build Name"`。

## Bug 與 Release Note

- `--redmine-create-bugs` 只可用於 `Fail` 與 `Error` 結果。
- `--redmine-issue-id` 不呼叫 Redmine API，只把既有 issue 記錄到 `Fail` 與 `Error` 的 execution notes。
- 若 bug 已先用 Redmine API 或 UI 建立，固定流程是先取得 issue id，再執行 `upload-report --redmine-issue-id <id> --write` 寫回 TestLink。
- 若環境中有 `REDMINE_URL`，可以只傳 `--redmine-issue-id`；否則請同時傳 `--redmine-issue-url`。
- execution notes 格式：

```text
REDMINE-ID: #<issue id>
REDMINE-URL: <issue url>
```

- 除非使用者明確要求 `--testlink-bug-link bugid` 或 `--testlink-bug-link both`，否則不要送出 TestLink 原生 `bugid`。
- XML-RPC 查不到自訂 release-note 內容時，不要宣稱內容不存在；請使用者提供 TestLink owner 支援的 API、database table 或存取方式。

## 安全規則

- 不使用 overwrite，除非未來版本明確實作並要求確認。
- 不刪除 executions。
- 只操作使用者指定的 project、test plan、platform、build、test suite。
- TestLink XML-RPC 失敗時，保留 fault code 與 fault message。
