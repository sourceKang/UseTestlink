# Redmine MCP

`redmine-mcp` is the Redmine/eITS-only adapter used by the multi-agent QA integration architecture. It has no TestLink devKey and does not make TestLink XML-RPC calls.

## Configuration

Required environment values:

```text
REDMINE_MCP_ENV_FILE=<absolute env file path>
REDMINE_ENV=corp|sandbox
REDMINE_URL=<Redmine base URL>
REDMINE_API_KEY=<personal API key>
REDMINE_PROJECT_ID=<default project identifier>
```

`REDMINE_ENV` is mandatory and fails closed when omitted. During migration, `TESTLINK_AGENT_ENV_FILE` remains a compatibility fallback, but new installations should use a separate `REDMINE_MCP_ENV_FILE` and keep TestLink credentials out of that file.

Do not place `REDMINE_ALLOW_MANAGER_FIELDS=true` in a shared env file. Manager-only fields remain blocked unless the MCP is running on an explicitly approved manager-owned machine.

## Tools

- `redmine_health`
- `redmine_search_issues`
- `redmine_get_project_metadata`
- `redmine_validate_template`
- `redmine_preview_bug`
- `redmine_create_bug`
- `redmine_preview_comment`
- `redmine_add_comment`

Bug creation and comments default to preview. A write call must include `write: true` and the exact `preview_digest` returned for the unchanged planned payload. The server recomputes dedupe state immediately before creation; if another actor created a matching issue after preview, the old digest is rejected and a new preview is required.

## Write Policy

- Open dedupe match: reuse the issue; never create a duplicate.
- Disabling dedupe is not supported by `redmine-mcp`.
- Closed dedupe match: block and require a human decision; never reopen automatically.
- No match: preview issue creation.
- Comments use an `issue.notes`-only payload.
- Status, assignee, fixed version, and issue closure are not modified automatically.
- Every attempted write first creates a redacted `started` audit and updates the same record with success or failure under `local/redmine_audit/` by default.

## Local Registration

```toml
[mcp_servers.redmine-mcp]
command = "python"
args = ["-m", "redmine_mcp.server"]
cwd = "D:\\UseTestlink"

[mcp_servers.redmine-mcp.env]
REDMINE_MCP_ENV_FILE = "D:\\UseTestlink\\local\\redmine_mcp.env"
```

Restart the Codex task after changing MCP registration so the `tools/list` snapshot is refreshed.

## Validation

All automated tests must use fake clients or local mock HTTP endpoints. CI must never call the corporate Redmine/eITS service.
