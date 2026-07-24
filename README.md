# TestLink + Redmine QA Integration Agent

`testlink-agent` is the QA integration layer between automation reports, TestLink, and
the corporate Redmine/eITS workflow. It is not TestLink itself and it is not a second
formal Redmine instance.

The supported architecture has three MCP ownership boundaries:

| Boundary | Responsibility | Credential ownership |
|---|---|---|
| `testlink-mcp` | TestLink discovery, protected testcase maintenance, and protected execution | TestLink only |
| `redmine-mcp` | Redmine/eITS metadata, dedupe, issue, and evidence comments | Redmine only |
| `qa-integration-agent` | Report validation, orchestration, preview, traceability, audit, and resume | No upstream API keys |

Versioned handoff schemas and preview digests live in `qa-mcp-contracts`. The legacy
`testlink-agent-mcp` combined server and CLI remain available only for migration and
rollback compatibility.

See `docs/architecture.md` for system boundaries, `docs/workflow.md` for the write and
resume flow, `docs/multi-agent-migration.md` for migration status,
`docs/cutover-runbook.md` for deployment gates, and `docs/deployment.md` for GitHub
release installation and upgrades. Authoritative assistant instructions live in
`AGENTS.md` and `.agents/skills/testlink-agent/SKILL.md`.

## Safety Invariants

- Every external write defaults to preview and must match the reviewed `preview_digest`.
- Testcase create/update defaults to one TestLink step row. Multi-row output requires
  both `single_step: false` and `allow_multi_row: true` in the reviewed preview.
- Testcase writes are successful only after TestLink readback matches the reviewed content.
- `corp` and `sandbox` are explicit; the coordinator rejects mixed environments.
- Redmine bug creation is opt-in with `redmine_create_bugs: true`.
- Fail/Error issues are deduplicated. Open matches are reused; closed matches block
  automatic creation or reopening.
- Result upload appends TestLink execution records by default.
- The agent never automatically closes an issue or changes status, assignee, or fixed
  version.
- Destructive TestLink operations require a separate explicit confirmation and are not
  part of the coordinator report-import path.
- Unknown report formats, unresolved platforms, and unresolved builds fail before any
  write. The coordinator never substitutes another platform or build.
- MCP responses, errors, and audit files redact devKeys, API keys, tokens, passwords,
  and known secret values.
- Formal Redmine work never silently falls back to Chrome when the Redmine MCP or its
  credential path is missing.
- Automated tests run offline and never call corporate TestLink or Redmine/eITS.

## Requirements And Installation

Requires Python 3.10+ and the Python standard library.

For repository development, use an editable installation only inside this checkout:

```powershell
python -m pip install -e .
```

For MCPs shared by other Codex projects, install a tagged GitHub release into an
isolated user environment. Do not point those projects at `D:\UseTestlink`:

```powershell
pipx install "git+https://github.com/sourceKang/UseTestlink.git@v1.5.0"
```

This installs the following console entrypoints:

- `testlink-mcp`
- `redmine-mcp`
- `qa-integration-agent-mcp`
- `testlink-agent` and `testlink-agent-mcp` for legacy compatibility

## Credential Separation

Keep credentials in ignored local files. Do not pass credentials in MCP tool arguments
and do not commit these files.

Create `local\testlink_mcp.env`:

```text
TESTLINK_AGENT_PROFILE=corp
TESTLINK_URL=https://your-testlink.example.com/testlink
TESTLINK_DEVKEY=<personal TestLink API key>
TESTLINK_AUTHOR_LOGIN=<TestLink login>
```

Create `local\redmine_mcp.env`:

```text
REDMINE_ENV=corp
REDMINE_URL=https://your-redmine.example.com
REDMINE_API_KEY=<personal Redmine API key>
REDMINE_PROJECT_ID=<project identifier>
REDMINE_TEMPLATE=local/redmine_templates/<project>.json
REDMINE_TRACKER_ID=<tracker ID>
REDMINE_PRIORITY_ID=<priority ID>
```

Use `sandbox` instead of `corp` only for development systems. A self-hosted Redmine is
a sandbox and must never be treated as the corporate defect workflow.

If credentials still exist in the old combined files, split only the allowlisted values
after reviewing the sources and destination paths:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\split_mcp_env.ps1 `
  -TestLinkSource local\testlink_agent.env `
  -RedmineSource .env `
  -Environment corp
```

The script writes `local\testlink_mcp.env` and `local\redmine_mcp.env` and refuses to
overwrite existing output unless `-Force` is explicitly supplied.

Manager-only Redmine fields remain blocked by default. Never put
`REDMINE_ALLOW_MANAGER_FIELDS=true` in a shared environment file; it is allowed only on
an approved manager-owned machine.

## Codex MCP Registration

For cross-project use, copy the entries from `docs/codex-mcp-config.example.toml` into
the user-level Codex `config.toml`. The executable names are resolved from the pipx
binary directory and no repository `cwd` is used:

```toml
[mcp_servers."testlink-mcp"]
command = "testlink-mcp"

[mcp_servers."testlink-mcp".env]
TESTLINK_MCP_ENV_FILE = "C:\\Users\\<username>\\.codex\\testlink-agent\\testlink_mcp.env"

[mcp_servers."redmine-mcp"]
command = "redmine-mcp"

[mcp_servers."redmine-mcp".env]
REDMINE_MCP_ENV_FILE = "C:\\Users\\<username>\\.codex\\testlink-agent\\redmine_mcp.env"

[mcp_servers."qa-integration-agent"]
command = "qa-integration-agent-mcp"

[mcp_servers."qa-integration-agent".env]
QA_TESTLINK_MCP_ENV_FILE = "C:\\Users\\<username>\\.codex\\testlink-agent\\testlink_mcp.env"
QA_REDMINE_MCP_ENV_FILE = "C:\\Users\\<username>\\.codex\\testlink-agent\\redmine_mcp.env"
```

The coordinator receives only the credential-file locations needed to start isolated
ownership-specific child MCP processes. It scrubs inherited TestLink and Redmine secret
variables and never accepts API keys through tool arguments.

Keep the credential files outside the Git checkout and replace `<username>` with the
local Windows account name. Restart Codex after changing MCP registration so the
available tool snapshot is refreshed. See `docs/deployment.md` for source and version
verification.

## Recommended Integrated Workflow

1. Use read-only `testlink-mcp` discovery tools to confirm the exact project, plan,
   platform, build, and test cases.
2. Call `qa_preview_report_import` with a stable `operation_id`, explicit environment,
   exact target, and report path. Preview performs zero external writes.
3. Review the resolved target, parsed counts, ignored rows, missing cases, Redmine
   create/reuse decisions, warnings, and returned `preview_digest`.
4. Only after explicit confirmation, call `qa_execute_report_import` with unchanged
   inputs, `write: true`, and the matching digest.
5. Validate TestLink/Redmine traceability and the item-level workflow audit.
6. For partial failure, call `qa_resume_report_import` with the same operation identity
   and matching audit; do not rerun the entire import as a new operation.

Changes to the report, target, environment, template, custom fields, or Redmine opt-in
invalidate the old digest and require a new preview.

## MCP Tool Boundaries

### `testlink-mcp`

Use for TestLink-only discovery, testcase maintenance, and protected execution. The
recommended execution tool is `testlink_report_execution`, which previews or appends one
execution using an exact target and confirmed digest. Read-only discovery includes
`testlink_list_projects`, `testlink_list_plans`, `testlink_list_platforms`,
`testlink_list_builds`, `testlink_list_suites`, and `testlink_find_suites`.

Use `testlink_create_testcase` and `testlink_update_testcase` for testcase maintenance.
Both tools default to preview, require an explicit environment and operation ID, bind the
row policy and payload into `preview_digest`, and verify the written testcase with
`getTestCase` before reporting success. The pure server does not expose the legacy
`create_test_case` or `update_test_case` bypass names.

The pure server does not expose the integrated `testlink_upload_report`, legacy
`report_result`/`report_results_batch`, `link_bug`, or `overwrite_result` paths. See
`docs/testlink-mcp-v2.md` for the complete boundary and configuration.

### `redmine-mcp`

Use for Redmine/eITS-only operations:

- `redmine_health`
- `redmine_search_issues`
- `redmine_get_project_metadata`
- `redmine_validate_template`
- `redmine_preview_bug` / `redmine_create_bug`
- `redmine_preview_comment` / `redmine_add_comment`

Bug and comment writes require `write: true` and the matching preview digest. Bug creation
accepts optional local image attachments whose content hash, filename, MIME type, and size
are bound into the preview. The server rechecks dedupe immediately before creation. See
`docs/redmine-mcp.md` and `docs/redmine-fields.md`.

### `qa-integration-agent`

Use for cross-system automation-report workflows:

- `qa_preview_report_import`
- `qa_execute_report_import`
- `qa_resume_report_import`
- `qa_get_operation`
- `qa_validate_traceability`
- `qa_compare_shadow_previews`

The coordinator uses versioned contracts, aggregates previews, preserves item-level
state for resume, and verifies bidirectional traceability. It does not own TestLink or
Redmine credentials.

## File Separation

Tracked source and documentation include:

- `qa_integration_agent/`
- `qa_mcp_contracts/`
- `redmine_mcp/`
- `testlink_mcp/`
- `testlink_agent_core/`
- `contracts/`
- `docs/`
- `tests/`
- `tools/`
- `.agents/`
- `AGENTS.md`
- `README.md`
- `.env.example`
- `testlink_agent.py`

Files for local use only are ignored by git:

- `.env`
- `local/`
- `downloads/`
- `reports/`
- `output/`
- `outputs/`
- `github_upload/` local GitHub upload staging folder; keep it locally, but do not commit it.
- downloaded testcase JSON files such as `testcases.json` or `ems_testcases.json`
- downloaded testcase Excel files such as `testcases.xlsx` or `ems_testcases.xlsx`
- credential files such as `local/testlink_mcp.env`, `local/redmine_mcp.env`, and
  legacy `local/testlink_agent.env`

Recommended local layout:

```text
D:\UseTestlink
  local\
    testlink_mcp.env
    redmine_mcp.env
    redmine_templates\
    testlink_audit\
    redmine_audit\
    qa_audit\
  downloads\
    ems_testcases.json
  reports\
    automation_report.txt
```

## GitHub Upload Package

This root folder is the local working copy. Keep personal files such as `.env`, downloaded
testcase exports, and automation reports here only.

Keep `github_upload/` as the local staging folder for future GitHub uploads. It is ignored by
git on purpose because its contents are generated from the shareable source files.

To prepare a clean GitHub-ready copy:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\sync_github_upload.ps1
```

The script rebuilds `github_upload/` from the shareable source files, then verifies that
local-only files such as `.env`, `downloads/`, `reports/`, and testcase/report exports were
not copied. Upload the contents of `github_upload/` to GitHub, not this whole local working
folder.

## Project Structure

- `qa_integration_agent/`: credential-free coordinator, stdio MCP ports, workflow
  audit, resume, traceability, and shadow comparison.
- `qa_mcp_contracts/`: canonical payload digests, operation identity, validation, and
  shared atomic file replacement.
- `redmine_mcp/`: Redmine-only REST client, policy, templates, dedupe, protected writes,
  redaction, and audit.
- `testlink_mcp/`: TestLink-only MCP adapter and protected execution path.
- `testlink_agent_core/`: shared TestLink/report domain code plus the legacy combined
  CLI and MCP compatibility implementation.
- `contracts/v1/`: versioned JSON schemas for previews, results, errors, operation
  context, and workflow audit.
- `docs/`: architecture, workflow, MCP registration, migration, field policy, and
  cutover guidance.
- `tests/`: offline unit, contract, server, coordinator, resume, and safety tests.

## Validation

Run the complete offline suite before publishing changes:

```powershell
python -m unittest discover -s tests
```

The Definition of Done also requires synchronized documentation, secret/repository
hygiene checks, and confirmation that no `.env`, `local/`, API key, devKey, or downloaded
report is staged.

## Legacy CLI Reference

The following CLI workflow remains available for migration, rollback, or environments
where MCP is unavailable. It uses the combined `TESTLINK_AGENT_ENV_FILE` /
`local/testlink_agent.env` configuration and is not the preferred new integration path.

`testlink_agent.py` keeps the direct command style:

```powershell
python .\testlink_agent.py list-projects
```

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
- Steps are collapsed into one TestLink step row by default, with numbered action and expected-result lines.
- Use `--no-single-step --allow-multi-row` together only when you intentionally want one
  TestLink row per supplied step. Either flag alone is rejected.
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

Replace steps using the default single TestLink row:

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
- Repeated `--step` entries are kept in one TestLink row by default.
- Use `--no-single-step --allow-multi-row` together only when you intentionally want one
  TestLink row per supplied step. Either flag alone is rejected.
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

To resume a partially failed write without repeating successful TestLink execution rows:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --resume-audit "local\audit\previous-upload-report.json" `
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

If the Redmine project requires custom fields, create one project-specific template
under `local/redmine_templates/` and pass it with `--redmine-template`. Use
`docs/redmine-template.example.json` as the shareable starting point, then replace the
example custom field IDs with the real Redmine custom field IDs for that project.

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore `
  --redmine-create-bugs `
  --redmine-template "local\redmine_templates\your-redmine-project.json"
```

Template values can reference report, TestLink, result, and environment data:

```text
{{header.EMS Version}}
{{context.plan.name}}
{{context.build.name}}
{{result.external_id}}
{{report_date}}
{{today}}
{{env.REDMINE_REPORTER}}
```

During preview, required custom fields are validated locally. If values such as
`Model`, `Customer`, or `Reporter` are missing, the command fails before calling the
Redmine create issue API.

To create/reuse Redmine bugs and record them in TestLink execution notes:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore `
  --redmine-create-bugs `
  --redmine-template "local\redmine_templates\your-redmine-project.json" `
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
- `--redmine-template` loads project-specific Redmine defaults and required custom fields.
- `--redmine-custom-field "12=value"` or `--redmine-custom-field "FW Ver=value"` overrides one template custom field. Field names require the template or override JSON to provide the Redmine custom field ID.
- `--redmine-issue-id` and `--redmine-issue-url` record an existing Redmine issue without calling the Redmine API.
- `--redmine-dedupe open` reuses an open issue with the same generated subject before creating a new one.
- `--testlink-bug-link notes` is the default and writes notes only.
- `--testlink-bug-link bugid` or `--testlink-bug-link both` can still be used for explicit native XML-RPC `bugid` testing.

## Share with Teammates

Publish a reviewed GitHub release tag. Teammates install that tag with pipx, keep their own credential env files outside the checkout, and register the installed executables in their user-level Codex configuration. They do not need a clone for normal MCP use.

Before making a fork or copy public, make sure examples, docs, logs, and test fixtures do not contain internal URLs, project names, platform names, report paths, or credentials.
