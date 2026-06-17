# TestLink Agent CLI

TestLink Agent CLI is a small Python XML-RPC helper for repeatable TestLink workflows.
It supports read-only lookup, testcase download, testcase creation, report upload, and
optional Redmine issue creation/linking.

## Setup

Requires Python 3.10+ and only the Python standard library.

Copy the example env file and fill in your own values:

```powershell
Copy-Item .env.example .env
```

Required TestLink values:

```powershell
TESTLINK_URL=https://your-testlink.example.com/testlink
TESTLINK_DEVKEY=your-personal-api-access-key
TESTLINK_AUTHOR_LOGIN=your-testlink-login
```

Optional Redmine values for automatic bug creation:

```powershell
REDMINE_URL=https://your-redmine.example.com
REDMINE_API_KEY=your-redmine-api-key
REDMINE_PROJECT_ID=redmine-project-identifier
REDMINE_TRACKER_ID=1
REDMINE_PRIORITY_ID=2
```

Use `--env-file .env` on commands, or set these values in your shell environment.

For repeated local use, create one ignored record file:

```powershell
New-Item -ItemType Directory -Force local
Copy-Item .\.env.example local\testlink_agent.env
```

After editing `local\testlink_agent.env`, commands can run without `--env-file`.
When `--env-file` is not given, the CLI loads credentials in this order: the file named in
`TESTLINK_AGENT_ENV_FILE`, then `.env`, then `local/testlink_agent.env`.

To share one credential record across multiple projects on the same machine, set:

```powershell
$env:TESTLINK_AGENT_ENV_FILE="D:\UseTestlink\local\testlink_agent.env"
```

## Project Structure

Run the tool directly with:

```powershell
python .\testlink_agent.py <command>
```

`testlink_agent.py` is a compatibility entrypoint. Core behavior lives in
`testlink_agent_core/`:

- `cli.py` builds argparse commands and handles top-level errors.
- `commands.py` contains command handlers.
- `clients.py` contains the TestLink XML-RPC client.
- `redmine.py` contains Redmine integration helpers.
- `reports.py`, `testcases.py`, `suites.py`, and `catalog.py` contain domain logic.
- `output.py` writes JSON and XLSX output.

## Safety Rules

- Do not commit `.env` or personal API keys.
- Most write commands preview by default. Add `--write` only after reviewing the JSON preview.
- Result upload appends execution records by default; it does not overwrite old executions.
- Redmine issue creation is opt-in with `--redmine-create-bugs`.
- Native TestLink `bugid` linking is off by default; Redmine IDs are recorded in execution notes unless explicitly requested.

## Lookup Commands

```powershell
python .\testlink_agent.py list-projects
```

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

```powershell
python .\testlink_agent.py list-suites `
  --project "YourProject"
```

```powershell
python .\testlink_agent.py refresh-catalog
```

```powershell
python .\testlink_agent.py find-suites `
  --project-contains "Gateway" `
  --suite-contains "VPN"
```

Save a frequent target as a local profile:

```powershell
python .\testlink_agent.py save-profile `
  --name gateway-vpn `
  --project "Gateway" `
  --suite-id 695420
```

Then view saved profiles:

```powershell
python .\testlink_agent.py list-profiles
```

## Download Testcases

```powershell
python .\testlink_agent.py download-testcases `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --out downloads\testcases.json
```

Use `--format xlsx --out downloads\testcases.xlsx` to export Excel.

## Create Testcase

Preview first:

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

Add `--write` only after the preview is correct.

If you saved a profile, preview can be shorter:

```powershell
python .\testlink_agent.py create-testcase `
  --profile gateway-vpn `
  --name "can_login" `
  --summary "Verify that a valid user can log in." `
  --step "Open the login page => Login form is shown"
```

Use `list-suites` first to find available suites in another project. If a suite name is
duplicated, use the exact `--suite-id` from `list-suites` instead of `--suite-name`.
Use `find-suites` when you only know partial project or suite text; it prints
ready-to-copy `create_args` for `create-testcase`. `find-suites` uses the local
`local/testlink_catalog.json` cache when it exists; add `--refresh` to update it or
`--offline` to avoid contacting TestLink.
Use `save-profile` for repeated project/suite targets; profiles are stored in
`local/testlink_profiles.json` and ignored by git.
Multi-line summary, preconditions, step actions, and expected results are converted
to TestLink rich-text line breaks.

## Update Testcase

Preview first. Only fields you specify are sent to TestLink:

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

Add `--write` only after the preview is correct. Use either `--testcase-id` or
`--testcase-external-id`; use `--version` when your TestLink instance requires a
specific testcase version.
Multi-line preconditions and step text are converted to TestLink rich-text line
breaks.

## Upload Report

Preview report upload:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "reports\automation_report.txt" `
  --skip-policy ignore
```

Write after reviewing preview:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "reports\automation_report.txt" `
  --skip-policy ignore `
  --write
```

Create or reuse Redmine issues for failed results:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "reports\automation_report.txt" `
  --skip-policy ignore `
  --redmine-create-bugs
```

## Local Files

These paths are ignored and are safe for local use:

- `.env`
- `local/`
- `downloads/`
- `reports/`
- `output/`
- cache/build artifacts

Only commit source files, docs, examples, and tests.
