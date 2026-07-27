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
```

`TESTLINK_AGENT_PROFILE` is mandatory and fails closed when omitted. Do not place Redmine credentials in the TestLink MCP env file.

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

Every attempted write creates a redacted `started` audit under `local/testlink_audit/` and updates the same record to success or failure.

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

Set `TESTLINK_MCP_TOOLSET` to `discovery`, `execution`, `maintenance`, `integration`, or
`all`. Codex should enable only the task-specific surface; `integration` is reserved for
the coordinator child process.

The v2 server excludes:

- `testlink_upload_report`
- legacy `report_result`
- legacy `report_results_batch`
- `link_bug`
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
TESTLINK_MCP_TOOLSET = "execution" # discovery, execution, maintenance, integration, all
TESTLINK_MCP_ENV_FILE = "D:\\UseTestlink\\local\\testlink_mcp.env"
```

Restart the Codex task after changing MCP registration so its tool snapshot is refreshed.
