# TestLink MCP v2

`testlink-mcp` v2 is the TestLink-only adapter in the multi-agent QA architecture. It does not expose report-to-Redmine orchestration and does not accept Redmine credentials or Redmine fields.

The legacy `testlink-agent-mcp` entrypoint remains available during migration. The `testlink-mcp` console entrypoint now targets the pure v2 server.

## Configuration

Use a TestLink-only env file:

```text
TESTLINK_MCP_ENV_FILE=<absolute env file path>
TESTLINK_AGENT_PROFILE=corp|sandbox
TESTLINK_URL=<TestLink URL>
TESTLINK_DEVKEY=<personal devKey>
TESTLINK_AUTHOR_LOGIN=<login>
TESTLINK_TESTER_ID=<your numeric TestLink user ID>      # required by testlink_link_bug only
TESTLINK_WEB_BASE_URL=<TestLink web root>              # optional, same host as TESTLINK_URL
```

`TESTLINK_AGENT_PROFILE` is mandatory and fails closed when omitted. Do not place Redmine credentials in the TestLink MCP env file.

`TESTLINK_TESTER_ID` is the TestLink user recorded as the tester of executions written by
`testlink_link_bug`. Set it to your own user ID; it has no default, and the tool fails with
`CONFIG_MISSING` (or `CONFIG_INVALID` when it is not numeric) instead of falling back to a
shared account. It is not a tool argument. `TESTLINK_WEB_BASE_URL` is only needed when the
web root cannot be derived from `TESTLINK_URL` (the part before `/lib/api/` or
`/index.php`); it must use http(s) on the same host and port as `TESTLINK_URL`.

## Protected Execution

Use `testlink_resolve_execution_target` when names need validation. It resolves the exact
project, plan, platform, build, and optional testcase in one read-only MCP call. Missing or
non-unique values fail; suggestions are diagnostic only and are never selected automatically.

`testlink_report_execution` requires:

- caller-generated `operation_id`
- explicit `environment`
- exact project, plan, platform, and build/build ID
- external testcase ID
- status and notes

The first call defaults to preview and returns a SHA-256 `preview_digest`. A write call must provide the same digest for the unchanged resolved target and TestLink payload. Target changes between preview and write require a new preview.

Execution writes append records. The protected tool does not expose overwrite or deletion. TestLink notes may contain Coordinator-generated Redmine traceability, but the TestLink MCP never calls Redmine.

TestLink's XML-RPC `reportTCResult` answers with a list of structs (for example
`[{"status": true, "id": 3043053}]`), not a bare mapping. The server reads the execution ID
from that shape, and also inspects the response for a rejection: an error struct (`code`
present or `status: false`) or an unrelated answer such as a platform list, which XML-RPC
returns instead of a result when the platform is missing or ambiguous. Either case raises a
failure instead of recording a successful execution.

Every attempted write creates a redacted `started` audit under `local/testlink_audit/` and updates the same record to success or failure.

## Protected Bug Linking

`testlink_link_bug` (execution toolset only, not `integration`) links Redmine bug IDs to one
testcase through the corporate custom endpoint `CA_set_result.php`, because the bug link
table (`execution_spr`) has no native XML-RPC write. The endpoint is an HTTP GET without a
devKey, sent directly (no proxy) to the web root derived from `TESTLINK_URL`.

Inputs: caller-generated `operation_id`, explicit `environment`, exact project, plan,
platform, and build names, a plain external testcase ID (`PRJ-1234`; a `_<n>` step suffix is
rejected because the endpoint would report only that step), 1-20 numeric Redmine `bug_ids`,
`status` (`p`, `f`, `b`), optional `notes`, and `execution_duration` (minutes, default 5).
The target is resolved with the same exact XML-RPC lookup as
`testlink_resolve_execution_target`; zero or multiple matches fail, and the testcase must be
in the selected plan/platform. The `CA_get_*` name endpoints are not used.

The first call is a preview and performs no write. It shows the resolved target, the testcase
(`id`, `name`, and `version_id` read from the plan row's `tcase_id`, `tcase_name`, and
`tcversion_id`), bug IDs, the status that will be written, `tester_id` (from
`TESTLINK_TESTER_ID`), notes, `execution_duration_minutes`, endpoint, and `preview_digest`,
plus these side effects:

- Linking always appends one TestLink execution with the given status; there is no
  link-only mode. A status other than `f` adds a preview warning. The execution duration is
  written in minutes and defaults to 5, as in the corporate reference script.
- The first link of a Redmine issue in a build makes TestLink add the Redmine note
  "The bug is still exist on <project> <build>, please double check the issue" to that issue.
  This also happens when the issue is Resolved or Closed, where RD may read it as a failed
  retest. The note does not change the issue's `updated_on`, so anything that detects Redmine
  changes by `updated_on` misses it; read the issue's journals to see it.
- TestLink does not confirm the link. Bug IDs unknown to the plan's issue tracker are skipped
  silently.

The preview also lists `pre_write_checks`. testlink-mcp holds no Redmine credentials, so the
caller reads each bug's status with redmine-mcp `redmine_get_issue` and tells the user when an
issue is Resolved or Closed. A missing testcase id or name adds a warning; verify the testcase
on the TestLink page before writing.

A write needs a boolean `write: true` (any other value stays a preview) and the unchanged
`preview_digest`. It records a redacted `started` audit, calls the endpoint once, requires the
response to echo the request (`issue_id:`), and reads the latest execution back with
`getLastExecutionResult` to compare status and tester. The result and audit are
`status=submitted`, `verification_status=link_unverified`: no known API reads
`execution_spr`, so the link must be checked on the TestLink page. A readback that does not
match adds a warning but does not change the outcome.

Resume and failure rules for one `operation_id`:

- Same digest already `submitted`: returns `skipped-resume` without sending again.
- Any `started` or `indeterminate` attempt (no echo, HTTP error, interrupted call):
  every later write fails closed with `LINK_BUG_INDETERMINATE`; check the testcase on the
  TestLink page, then use a new `operation_id` only if the link is missing.
- `submitted` with a different digest: `LINK_BUG_OPERATION_REUSED`; use a new `operation_id`.

This tool never closes Redmine issues or changes their status, assignee, or version.

## Batch Latest Results

`testlink_get_latest_results` is read-only and is in `lookup`, `execution`, and `discovery`. It
resolves the exact project, plan, platform, and build once, then reads the latest execution of
every requested testcase on that build and platform:

- Pass `testcase_external_ids` (up to 1000 plain IDs) or `all_in_plan=true`, not both.
- `status_counts` summarizes the results; `n` means the testcase has no execution on that build.
- With `expected_status` (`p`, `f`, or `b`), `rows` lists only the testcases that differ and
  `rows_omitted` counts the rest, so verifying a 254-case import costs one call and a short answer.
  `all_match_expected` is true only when every testcase matched and none was missing or failed.
- Testcases outside the plan/platform go to `not_in_plan`; a read error for one testcase goes to
  `errors` without failing the others. Each testcase is still one `getLastExecutionResult` call to
  TestLink, so large batches take time but not conversation tokens.

The `lookup` toolset is the read side of `execution` (target resolution, project/plan/platform/build
lists, testcase reads, and latest results) without the legacy duplicates and catalog tools that
`discovery` also carries.

## Protected Build Creation

`testlink_create_build` creates one build in an exact project and plan. It is in the
`execution` toolset (and therefore in `manual-qa` and `batch-import`).

- Preview is the default. It resolves the exact project and plan, compares the name with the
  plan's existing builds (ignoring case and repeated spaces), and reports `duplicate_found`
  with the matching builds; a duplicate makes `planned_write=false`.
- The name must not start or end with spaces; `release_date` must be `YYYY-MM-DD`; `active`
  and `open` are booleans (default true).
- A write needs the unchanged `preview_digest`. It re-checks for a duplicate (`BUILD_EXISTS`),
  writes a `started` audit, calls `tl.createBuild`, and reads the plan's builds back. Only
  exactly one matching build (with the returned id, when TestLink returns one) is `verified`.
- An explicit TestLink error struct is recorded as `failed` (`BUILD_REJECTED`). A transport
  error after the call is `indeterminate`; a missing readback is `verification_failed`
  (`BUILD_READBACK_MISMATCH`). A retry with the same operation and digest reads the builds first:
  it records success when exactly one matching build exists, and otherwise refuses with
  `BUILD_WRITE_INDETERMINATE` instead of creating a possible duplicate.

## Protected Testcase Maintenance

The pure server exposes `testlink_create_testcase` and `testlink_update_testcase`. Both
require a caller-generated `operation_id`, explicit `corp|sandbox` environment, preview
first behavior, and a matching `preview_digest` for write.

Logical steps default to one TestLink row:

```text
single_step=true,  allow_multi_row=false -> one row
single_step=false, allow_multi_row=false -> reject
single_step=false, allow_multi_row=true  -> explicitly authorized multiple rows
single_step=true,  allow_multi_row=true  -> reject inconsistent policy
```

Before write, the server validates the final payload row count and matching non-empty
Action/Expected numbering. After create/update, it resolves the testcase identity, calls
`getTestCase`, normalizes TestLink rich-text line breaks, and compares every written field.
Only an exact normalized match returns `verification_status=verified`.

If TestLink accepted the XML-RPC write but readback differs, the tool returns an error and
records `verification_failed` with redacted expected/readback digests and mismatch fields.
It does not overwrite or delete content. A retry with the same operation identity reads the
audited testcase first and refuses a possible duplicate write when the outcome cannot be
proven.

## Tool Boundary

Set `TESTLINK_MCP_TOOLSET` to one name or a comma-separated list. Each name is a toolset
(`discovery`, `lookup`, `execution`, `maintenance`, `integration`, `all`) or a usage-profile preset, and
the server exposes the union. Unknown or empty names fail closed. Codex and Claude should enable
only the task-specific surface; `integration` is reserved for the coordinator child process.

| Preset | testlink-mcp toolsets | For |
|---|---|---|
| `batch-import` | `execution`, `maintenance` | automation result import, build creation, testcase upkeep |
| `manual-qa` | `execution` | per-testcase results and Redmine bug links |
| `bug-tracking` | `lookup` | read-only lookups |

The same preset names select the matching redmine-mcp surface (see `docs/redmine-mcp.md`), so a
project can set one profile name on every server it registers.

When `TESTLINK_MCP_TOOLSET` is unset, the server exposes only the read-only `discovery`
toolset. Write tools (`execution`, `maintenance`) and `all` — which also carries legacy tools
such as `delete_execution` and `add_case_to_plan` — must be selected explicitly. Releases up to
v1.9.0 defaulted to `all`, so a registration without the variable exposed those tools.

`TESTLINK_MCP_BATCH_SESSION=1` is also reserved for that child: qa-integration-agent sets it
only on the child it keeps open for one preview/execute batch, so the child authenticates and
resolves the exact target once for that batch. Do not set it on a long-running server; without
it every call resolves the target again.

The v2 server excludes:

- `testlink_upload_report`
- legacy `report_result`
- legacy `report_results_batch`
- legacy `link_bug` (replaced by the protected `testlink_link_bug`)
- legacy `create_build` (replaced by the protected `testlink_create_build`)
- `overwrite_result`
- legacy `create_test_case`
- legacy `update_test_case`

The compatibility server continues to expose the old tools until the Coordinator migration and shadow validation are complete.

## Local Registration

```toml
[mcp_servers.testlink-mcp]
command = "python"
args = ["-m", "testlink_mcp.server"]
cwd = "D:\\UseTestlink"

[mcp_servers.testlink-mcp.env]
TESTLINK_MCP_TOOLSET = "execution" # discovery, execution, maintenance, integration, all, or a preset
TESTLINK_MCP_ENV_FILE = "D:\\UseTestlink\\local\\testlink_mcp.env"
```

Restart the Codex task after changing MCP registration so its tool snapshot is refreshed.
