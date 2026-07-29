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
REDMINE_TEMPLATE=<absolute validated project template path>
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

### Description and comment format validation

The selected `REDMINE_TEMPLATE` declares a `text_format` contract. Corporate writes require
`validation: strict`. Bug descriptions and evidence comments are checked before preview;
high-confidence Textile syntax in a Markdown profile blocks the write, while code fences,
indented code, and `<pre>` regions are ignored. The MCP reports structured line findings,
binds the contract and findings into `preview_digest`, and never performs silent conversion.

Comments use the same default template resolved from `REDMINE_TEMPLATE`; callers may select
an explicit validated `template_file`, but cannot override the formatter directly.

### Image attachments on bug creation

`redmine_preview_bug` and `redmine_create_bug` accept an optional `attachments` array:

```json
{
  "attachments": [
    {
      "file": "D:\\evidence\\filter-result.png",
      "filename": "filter-result.png",
      "description": "Device list after applying the Submap Name filter"
    }
  ]
}
```

Supported formats are PNG, JPEG, GIF, WebP, and BMP. At most five images may be supplied,
and each image is limited to 10 MiB. The MCP detects the format from file content rather
than trusting the extension. Preview reads no Redmine data beyond the normal dedupe check
and performs no upload. It returns the filename, detected MIME type, byte size, SHA-256,
and attachment action; these values are part of `preview_digest`.

On a confirmed create, `redmine-mcp` uploads each binary to Redmine's `/uploads.json`
endpoint and places the returned internal upload references in the issue creation request.
Upload tokens never appear in MCP results, errors, or audit JSON. If the dedupe check finds
an existing open issue, it is reused and the supplied images are deliberately not uploaded,
preventing duplicate evidence attachments; preview reports `not-uploaded-reused` and a
warning. Adding images to an existing issue is outside this create-bug capability.

## Write Policy

- Open dedupe match: reuse the issue; never create a duplicate.
- Image attachments are uploaded only for a newly created issue and only after digest confirmation.
- A changed image, filename, description, size, or detected MIME type invalidates the preview digest.
- Disabling dedupe is not supported by `redmine-mcp`.
- Closed dedupe match: block and require a human decision; never reopen automatically.
- No match: preview issue creation.
- Comments use an `issue.notes`-only payload.
- Status, assignee, fixed version, and issue closure are not modified automatically.
- Every attempted write first creates a redacted `started` audit and updates the same record with success or failure under `local/redmine_audit/` by default.

## Local Registration

Direct Redmine registration is intended only for an explicit Redmine-only task profile;
the normal integrated profile registers `qa-integration-agent` alone.

```toml
[mcp_servers.redmine-mcp]
command = "redmine-mcp"

[mcp_servers.redmine-mcp.env]
REDMINE_MCP_TOOLSET = "issue" # issue, metadata, integration, all
REDMINE_MCP_ENV_FILE = "C:\\Users\\<username>\\.codex\\testlink-agent\\redmine_mcp.env"
```

Restart the Codex task after changing MCP registration so the `tools/list` snapshot is refreshed.

## Validation

All automated tests must use fake clients or local mock HTTP endpoints. CI must never call the corporate Redmine/eITS service.
