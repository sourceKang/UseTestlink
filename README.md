# TestLink Agent CLI

TestLink Agent CLI is a small Python XML-RPC helper for department TestLink workflows.
It is designed for Codex/agent use and for engineers who need a repeatable way to preview
and upload automation results.

## Safety Rules

- Do not commit personal API keys.
- Each user must use their own TestLink `Personal API access key`.
- The default `upload-report` mode is preview only. Add `--write` only after reviewing the preview.
- Redmine bug creation is opt-in. Add `--redmine-create-bugs` and `--write` only after reviewing the preview.
- Result upload appends execution records by default; it does not use overwrite.
- When Redmine bug creation is enabled, the CLI records the Redmine ID/URL in execution notes by default. It does not link `bugid` to the TestLink testcase unless explicitly requested.
- Custom release-note pages may not be available through the native TestLink XML-RPC API used by this CLI. Update this tool only after the TestLink owner provides the supported API, database table, or access method.
- Destructive actions such as deletion or overwrite are intentionally not implemented in this CLI.

## Setup

Requires Python 3.10+ and only the Python standard library.

Create environment variables:

```powershell
$env:TESTLINK_URL="https://your-testlink.example.com/testlink"
$env:TESTLINK_DEVKEY="your-personal-api-access-key"
```

Optional Redmine variables for automatic bug creation:

```powershell
$env:REDMINE_URL="https://your-redmine.example.com"
$env:REDMINE_API_KEY="your-redmine-api-key"
$env:REDMINE_PROJECT_ID="redmine-project-identifier"
$env:REDMINE_TRACKER_ID="1"
$env:REDMINE_PRIORITY_ID="2"
```

For repeated local use, create a shared local record file instead:

```powershell
New-Item -ItemType Directory -Force local
Copy-Item .\.env.example local\testlink_agent.env
```

`.env.example` is the TestLink agent credential template. Edit
`local\testlink_agent.env` with your `TESTLINK_URL`, `TESTLINK_DEVKEY`,
`TESTLINK_AUTHOR_LOGIN`, and optional Redmine settings.

You can point any command at a specific file with `--env-file <path>`. When
`--env-file` is not given, the CLI loads credentials in this order: the file named in
`TESTLINK_AGENT_ENV_FILE`, then `.env`, then `local/testlink_agent.env`. So other
commands and project scripts do not need to pass keys again.

If another project keeps the record file elsewhere, set one environment variable once:

```powershell
$env:TESTLINK_AGENT_ENV_FILE="D:\UseTestlink\local\testlink_agent.env"
```

## Agent and MCP Usage

This project can be used by agents in two layers:

- Primary: MCP server tools from `testlink-agent-mcp`
- Secondary: Codex skill instructions in `skills/testlink-agent/SKILL.md`

Install the package locally when you want console entrypoints:

```powershell
python -m pip install -e .
```

Example MCP server config:

```json
{
  "mcpServers": {
    "testlink-agent": {
      "command": "python",
      "args": ["-m", "testlink_agent_core.mcp_server"],
      "cwd": "D:\\UseTestlink",
      "env": {
        "TESTLINK_AGENT_ENV_FILE": "D:\\UseTestlink\\local\\testlink_agent.env"
      }
    }
  }
}
```

If you installed the package, the command can be shortened:

```json
{
  "mcpServers": {
    "testlink-agent": {
      "command": "testlink-agent-mcp",
      "cwd": "D:\\UseTestlink"
    }
  }
}
```

MCP tools exposed by this server:

- `testlink_list_projects`
- `testlink_list_plans`
- `testlink_list_platforms`
- `testlink_list_builds`
- `testlink_list_suites`
- `testlink_find_suites`
- `testlink_refresh_catalog`
- `testlink_download_testcases`
- `testlink_list_profiles`
- `testlink_save_profile`
- `testlink_delete_profile`
- `testlink_create_testcase`
- `testlink_update_testcase`
- `testlink_upload_report`

Write-capable tools default to preview mode. Agents should call `testlink_create_testcase`,
`testlink_update_testcase`, and `testlink_upload_report` with `write: false`, summarize the
preview, and call again with `write: true` only after explicit user confirmation.

## File Separation

Files that are safe to share or upload with this tool:

- `testlink_agent.py`
- `testlink_agent_core/`
- `README.md`
- `AGENT.md`
- `.gitignore`
- `.env.example`
- `tests/`
- `tools/`

Files for local use only are ignored by git:

- `.env`
- `local/`
- `downloads/`
- `reports/`
- `output/`
- `outputs/`
- `github_upload/`
- downloaded testcase JSON files such as `testcases.json` or `ems_testcases.json`
- downloaded testcase Excel files such as `testcases.xlsx` or `ems_testcases.xlsx`
- shared local record files such as `local/testlink_agent.env`

Recommended local layout:

```text
D:\UseTestlink
  .env
  downloads\
    ems_testcases.json
  reports\
    automation_report.txt
```

## GitHub Upload Package

This root folder is the local working copy. Keep personal files such as `.env`, downloaded
testcase exports, and automation reports here only.

To prepare a clean GitHub-ready copy:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\sync_github_upload.ps1
```

The script rebuilds `github_upload/` from the shareable source files, then verifies that
local-only files such as `.env`, `downloads/`, `reports/`, and testcase/report exports were
not copied. Upload the contents of `github_upload/` to GitHub, not this whole local working
folder.

## Project Structure

`testlink_agent.py` is a compatibility entrypoint. It keeps the direct command style:

```powershell
python .\testlink_agent.py list-projects
```

Core behavior lives in `testlink_agent_core/`:

- `cli.py` builds argparse commands and handles top-level errors.
- `commands.py` contains command handlers.
- `clients.py` contains the TestLink XML-RPC client.
- `redmine.py` contains Redmine integration helpers.
- `reports.py`, `testcases.py`, `suites.py`, and `catalog.py` contain domain logic.
- `output.py` writes JSON and XLSX output.

## List Projects

```powershell
python .\testlink_agent.py list-projects
```

## Look Up Plans, Platforms, Builds, Suites, and Profiles

Use these read-only commands when you do not know the exact TestLink names or IDs.

```powershell
python .\testlink_agent.py list-plans --project "YourProject"
```

```powershell
python .\testlink_agent.py list-platforms `
  --project "YourProject" `
  --plan "Your Test Plan"
```

```powershell
python .\testlink_agent.py list-builds `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --open-only
```

Most users can omit `--build` and `--build-id` during report upload. The CLI will use the latest active/open build and show the selected build in preview.

To find the `suite-id` needed for testcase creation in any project:

```powershell
python .\testlink_agent.py list-suites `
  --project "YourProject"
```

To search projects and suites together, and print ready-to-copy `create-testcase` args:

```powershell
python .\testlink_agent.py refresh-catalog
```

Then search locally:

```powershell
python .\testlink_agent.py find-suites `
  --project-contains "Gateway" `
  --suite-contains "VPN"
```

`find-suites` uses `local/testlink_catalog.json` when it exists. Add `--refresh` to update it
before searching, or `--offline` to require local-only search.

Save a frequent target as a local profile:

```powershell
python .\testlink_agent.py save-profile `
  --name gateway-vpn `
  --project "Gateway" `
  --suite-id 695420
```

Or create a profile from a catalog search:

```powershell
python .\testlink_agent.py save-profile `
  --name gateway-vpn `
  --project-contains "Gateway" `
  --suite-contains "VPN" `
  --offline
```

Profiles are stored in `local/testlink_profiles.json`, which is ignored by git.

## Download Test Cases

Download the test cases assigned to a test plan and platform as JSON:

```powershell
python .\testlink_agent.py download-testcases `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --out testcases.json
```

Omit `--out` to print JSON to stdout. Existing output files are not overwritten unless you add `--force`.

To download directly as Excel:

```powershell
python .\testlink_agent.py download-testcases `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --format xlsx `
  --out testcases.xlsx
```

## Create a Test Case

Create operations are preview-only by default. Use `--write` only after reviewing the payload.

Set the author login once:

```powershell
$env:TESTLINK_AUTHOR_LOGIN="your-testlink-login"
```

Preview a new test case:

```powershell
python .\testlink_agent.py create-testcase `
  --project "YourProject" `
  --suite-name "Test_Case_Group" `
  --name "can_login" `
  --summary "Verify that a valid user can log in." `
  --step "Open the login page => Login form is shown" `
  --step "Submit valid credentials => Dashboard is shown" `
  --importance high `
  --execution-type automated
```

If you saved a profile, the target can be much shorter:

```powershell
python .\testlink_agent.py create-testcase `
  --profile gateway-vpn `
  --name "can_login" `
  --summary "Verify that a valid user can log in." `
  --step "Open the login page => Login form is shown"
```

After the preview looks correct, add `--write`:

```powershell
python .\testlink_agent.py create-testcase `
  --project "YourProject" `
  --suite-name "Test_Case_Group" `
  --name "can_login" `
  --summary "Verify that a valid user can log in." `
  --step "Open the login page => Login form is shown" `
  --step "Submit valid credentials => Dashboard is shown" `
  --importance high `
  --execution-type automated `
  --write
```

Useful options:

- `--author-login` overrides `TESTLINK_AUTHOR_LOGIN`.
- `--profile` fills the saved `--project` and `--suite-id` from `local/testlink_profiles.json`.
- `list-profiles` shows saved profiles; `delete-profile --name <profile>` removes one.
- `--suite-name` accepts an exact suite name or path from `list-suites`; use `--suite-id` when names are duplicated.
- `find-suites` returns `create_args` such as `["--project", "Gateway", "--suite-id", "695420"]` for copying into `create-testcase`.
- `refresh-catalog` stores project/suite lookup data in `local/testlink_catalog.json`; this local cache is ignored by git.
- `--summary-file` and `--preconditions-file` read UTF-8 text from files.
- `--steps-file` reads a JSON array of strings or objects with `actions`, `expected_results`, and optional `execution_type`.
- Multi-line summary, preconditions, step actions, and expected results are converted to TestLink rich-text line breaks.
- `--duplicate-action block` is the default; use `--duplicate-action generate-new` only when you intentionally want TestLink to create a renamed duplicate.

## Update a Test Case

Update operations are also preview-only by default. Only fields you specify are sent to
TestLink; omitted fields are left unchanged.

Preview a summary update by external testcase ID:

```powershell
python .\testlink_agent.py update-testcase `
  --profile gateway-vpn `
  --testcase-external-id "GW-123" `
  --summary "Updated summary text."
```

Replace steps:

```powershell
python .\testlink_agent.py update-testcase `
  --profile gateway-vpn `
  --testcase-external-id "GW-123" `
  --step "Open VPN page => VPN page is shown" `
  --step "Connect VPN => Connection succeeds"
```

After the preview looks correct, add `--write`.

Useful options:

- Use either `--testcase-id` for the internal TestLink ID or `--testcase-external-id` for IDs such as `GW-123`.
- `--version` can target a specific testcase version when your TestLink instance requires it.
- `--summary-file`, `--preconditions-file`, and `--steps-file` work the same way as `create-testcase`.
- `--step` and `--steps-file` replace the testcase steps with the supplied steps.
- Multi-line preconditions and step text are converted to TestLink rich-text line breaks.

## Preview a Report Upload

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore
```

You can still specify a build explicitly when needed:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --build "1.2.3 build 5" `
  --report "C:\path\to\report.txt"
```

Preview validates:

- devKey
- project, test plan, platform, and build
- build is active/open
- report rows can map to test cases in the target plan/platform
- duplicate or missing external IDs

## Upload a Report

Add `--write` after the preview looks correct:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore `
  --write
```

Status mapping:

| Report result | TestLink status |
| --- | --- |
| `Pass` | `p` |
| `Fail` | `f` |
| `Error` | `f` |
| `Blocked` | `b` |
| `Skip` with `--skip-policy ignore` | not uploaded |
| `Skip` with `--skip-policy blocked` | `b` |

Execution notes include automation source, report generation time, EMS version, node, test function, original result, duration, and report filename.

## Create or Link Redmine Bugs

To preview which Redmine bugs would be created or reused for failed results:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore `
  --redmine-create-bugs
```

To create/reuse Redmine bugs and record them in TestLink execution notes:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore `
  --redmine-create-bugs `
  --write
```

To record an existing Redmine issue without creating a new Redmine bug:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore `
  --redmine-issue-id 255162 `
  --redmine-issue-url "https://redmine.example.com/issues/255162"
```

After the preview is correct, add `--write` to write the TestLink execution result and note:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore `
  --redmine-issue-id 255162 `
  --redmine-issue-url "https://redmine.example.com/issues/255162" `
  --write
```

By default this records `REDMINE-ID` / `REDMINE-URL` in notes only. If `--redmine-issue-url` is omitted, the CLI builds the URL from `REDMINE_URL`.

For each `Fail` or `Error`, the CLI creates/reuses a Redmine issue or uses the issue from `--redmine-issue-id`, writes the TestLink execution, and appends `REDMINE-ID` / `REDMINE-URL` to the execution notes. Native TestLink `bugid` linking is intentionally off by default because some TestLink deployments use a custom Redmine linkage table that XML-RPC `bugid` does not populate.

Useful options:

- `--redmine-project`, `--redmine-tracker-id`, `--redmine-priority-id`, `--redmine-assigned-to-id`
- `--redmine-issue-id` and `--redmine-issue-url` record an existing Redmine issue without calling the Redmine API.
- `--redmine-dedupe open` reuses an open issue with the same generated subject before creating a new one.
- `--testlink-bug-link notes` is the default and writes notes only.
- `--testlink-bug-link bugid` or `--testlink-bug-link both` can still be used for explicit native XML-RPC `bugid` testing.

## Share with Teammates

Publish this repository to the location your team uses for shared tooling. Teammates can clone it, set their own `TESTLINK_URL` and `TESTLINK_DEVKEY`, and run the same commands from their own agent or terminal.

Before making a fork or copy public, make sure examples, docs, logs, and test fixtures do not contain internal URLs, project names, platform names, report paths, or credentials.
