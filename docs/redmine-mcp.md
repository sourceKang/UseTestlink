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
- `redmine_get_issue`
- `redmine_list_projects`
- `redmine_get_project_metadata`
- `redmine_validate_template`
- `redmine_preview_bug`
- `redmine_create_bug`
- `redmine_preview_comment`
- `redmine_add_comment`

Bug creation and comments default to preview. A write call must include `write: true` and the exact `preview_digest` returned for the unchanged planned payload. The server recomputes dedupe state immediately before creation; if another actor created a matching issue after preview, the old digest is rejected and a new preview is required.

### Reading existing issues and resolving projects

`redmine_get_issue` returns full issue content for one `issue_id`: description, status, tracker,
priority, project, author, assignee, category, fixed version, custom fields, and the journal
(comment/change) history with attachments metadata. It never returns watchers, spent/estimated
hours, or any other field beyond this safe projection, and it never writes. Journal details carry
`field_name` for custom-field changes and `old_label`/`new_label` for status changes; a field hidden
from the current tracker has `field_name: null`. Custom field names are whitespace-trimmed.

`redmine_list_projects` pages through every project server-side and returns
`id`/`identifier`/`name`/`status`, plus `total_count`, `fetched_count`, and `truncated` (true only
if the 20-page safety cap was reached). An optional `query` narrows the result by name or
identifier substring, so a caller resolves the correct `project_id` instead of guessing a slug.

`redmine_search_issues` returns safe summaries (`id`/`subject`/`status`/`updated_on`/`url`) with
`total_count` and `offset` for paging. Optional filters: `subject_contains`, `custom_field_filters`
(`[{id, value, match: exact|contains}]`, sent as Redmine `cf_<id>`), `author_id`,
`assigned_to_id`, `category_id`, and inclusive `updated_from`/`updated_to`/`closed_from`/`closed_to`
dates (`YYYY-MM-DD`). `include_custom_fields` returns the listed custom field values per issue.
Redmine silently ignores a filter on a custom field that is not filterable or not visible, so every
returned issue is checked against each custom field filter; any mismatch fails the whole call with
`FILTER_NOT_APPLIED` rather than returning unfiltered results.
`status_id` is passed to Redmine unchanged: `open` and `closed` follow each status's server-side
`is_closed` flag, not its name. On the corporate Redmine, `Resolved` is not closed, so `open`
returns New and Resolved issues alike; pass a specific status ID to narrow the result.

### Content redaction on read

Issue text is authored by people and can contain device or service credentials. Before any read
result leaves the server, every string in `redmine_get_issue` and `redmine_search_issues` output
is masked for: `password=`/`passwd=`/`pwd=`/`secret=`/`token=`/`api_key=` assignments,
`--password`/`--passwd`/`--pass` flags, `sshpass -p`, a `-p` value on a line that also has
`-u`/`--user`/`--username` (so `ssh -p 22` stays readable), URL `user:password@` userinfo, and the
local part of email addresses (`*****@example.com`). Results include `redactions` with
`credentials` and `emails` counts so callers know content was masked. Redmine's own stored text is
never modified; HTML entities such as `&lt;` in descriptions are returned as stored.

### Description and comment format validation

The selected `REDMINE_TEMPLATE` declares a `text_format` contract. Corporate writes require
`validation: strict`. Bug descriptions and evidence comments are checked before preview;
high-confidence Textile syntax in a Markdown profile blocks the write, while code fences,
indented code, and `<pre>` regions are ignored. The MCP reports structured line findings,
binds the contract and findings into `preview_digest`, and never performs silent conversion.

Comments use the same default template resolved from `REDMINE_TEMPLATE`; callers may select
an explicit validated `template_file`, but cannot override the formatter directly.

### Attachments on bugs and comments

`redmine_preview_bug`, `redmine_create_bug`, `redmine_preview_comment`, and
`redmine_add_comment` accept the same optional `attachments` array:

```json
{
  "attachments": [
    {
      "file": "D:\\evidence\\trigger.webm",
      "filename": "trigger.webm",
      "description": "31-second recording of the trigger"
    },
    {"file": "D:\\evidence\\repro.py"}
  ]
}
```

| Kind | Formats | Count | Per file |
|------|---------|-------|----------|
| Image | PNG, JPEG, GIF, WebP, BMP | 5 | 10 MiB |
| Video | WebM, MKV, MP4/M4V, MOV | 3 | 20 MiB |
| Text | `.py`, `.sh`, `.json`, `.jsonl`, `.log`, `.txt`, `.csv`, `.md` | 5 | 1 MiB |

At most 10 files and 64 MiB in total per write. Limits are rejections, never truncation.
The company Redmine `attachment_max_size` is not readable through the REST API, so these
limits are local constants (`redmine_mcp/attachments.py`). If Redmine's own limit is lower,
`/uploads.json` fails with HTTP 422 before the issue or comment is written, and the failure
is audited.

Type detection:

- Images and videos are detected from file content (magic bytes: WebM/Matroska EBML
  header `1A 45 DF A3` with DocType `webm` or `matroska`; MP4/MOV `ftyp` box). The filename
  extension must match the detected type, so a binary cannot be renamed into another kind.
- Any other file is text only if its extension is on the list above. Text must be UTF-8
  (a BOM is allowed) and must not contain NUL bytes.
- Text files are scanned for credential-like lines, but a hit does **not** block the upload:
  attachments are evidence for RD, who may need the test accounts in a repro script or log to
  reproduce the problem. The file's `content_scan` becomes `flagged` with `finding_count` and up
  to 20 `{line, rule}` findings, and the preview adds a warning naming the file, line numbers and
  rules; the matched value is never returned, logged or audited. The file is uploaded unchanged
  once the user confirms the preview; to keep a value out of Redmine, mask the file and preview
  again. Rules cover
  `key=value`, `key: value`, JSON fields, and headers for password, passphrase, secret,
  API key, devKey, token (including `csrftoken`, `access_token`), session ID, registration
  ID, and private key; `Bearer`/`Basic`/`Digest` credentials; `--password`, `sshpass -p`, and
  `-p` beside a user flag; URL user info; private-key blocks; JWTs without any label
  (`eyJ….eyJ….…`); and the configured `REDMINE_API_KEY`/`TESTLINK_DEVKEY` values.
  Chinese labels (密碼、密码、帳密、帳號密碼、口令、金鑰、密鑰、權杖、令牌) count when followed
  by `：`, `:`, `=`, `為` or `是` and an ASCII value (`密碼：abc123`), and `帳密 admin/abc123`
  counts without a separator; prose such as `密碼：見附件` or `密碼是否正確` does not.
  Placeholders such as `*****`, `<password>`, `$EMS_PASS`, `${X}`, `{token}`,
  `os.environ[...]`, `null`, `N/A`, and empty values are not hits.
  In `.py` files only string literals count for English keys, so `password=args.password` is
  not a hit; Chinese labels count everywhere, including comments.
  The scan only points at likely lines; it is not a guarantee, so still review the attachments.
- Videos and images are not scanned.

Preview performs no upload. It returns each file's `filename`, `description`, `kind`,
detected `content_type`, `size`, `sha256`, and for text a `content_scan` summary; these values
are part of `preview_digest`, so changing a file after preview invalidates the digest.

On a confirmed write, `redmine-mcp` uploads each file to Redmine's `/uploads.json` endpoint
and passes the returned upload references in the same request: `issue.uploads` on creation,
`issue.notes` plus `issue.uploads` on a comment. Upload tokens never appear in MCP results,
errors, or audit JSON. If the bug dedupe check finds an existing open issue, it is reused and
the supplied files are deliberately not uploaded (`not-uploaded-reused` with a warning); add
evidence to that issue with `redmine_add_comment` and `attachments` instead.

## Write Policy

- Open dedupe match: reuse the issue; never create a duplicate.
- Attachments are uploaded only after digest confirmation: on bug creation only for a newly created issue, on comments together with the notes.
- A changed file, filename, description, size, or detected MIME type invalidates the preview digest.
- Disabling dedupe is not supported by `redmine-mcp`.
- Closed dedupe match: block and require a human decision; never reopen automatically.
- No match: preview issue creation.
- Comments use an `issue.notes` payload, plus `issue.uploads` only when attachments were confirmed.
- Status, assignee, fixed version, and issue closure are not modified automatically.
- Every attempted write first creates a redacted `started` audit and updates the same record with success or failure under `<data dir>/redmine_audit/` by default (`%LOCALAPPDATA%\testlink-agent` or `TESTLINK_AGENT_DATA_DIR`; see `docs/workflow.md` Audit Locations).
- Comment retry with the same `operation_id` and `preview_digest`: an `added` audit is skipped; a `started` or `failed` audit is first checked against the issue journals by notes digest. A matching journal marks the audit `added` (`recovered`) and skips the write, so a timeout after Redmine applied the comment does not post it twice. With no match, `started` fails closed (`INDETERMINATE_OPERATION`) and `failed` retries the write.

## Local Registration

Direct Redmine registration is intended only for an explicit Redmine-only task profile;
the normal integrated profile registers `qa-integration-agent` alone.

```toml
[mcp_servers.redmine-mcp]
command = "redmine-mcp"

[mcp_servers.redmine-mcp.env]
REDMINE_MCP_TOOLSET = "issue" # discovery, issue, metadata, integration, all, or a preset
REDMINE_MCP_ENV_FILE = "C:\\Users\\<username>\\.codex\\testlink-agent\\redmine_mcp.env"
```

Restart the Codex task after changing MCP registration so the `tools/list` snapshot is refreshed.

## Toolsets

`REDMINE_MCP_TOOLSET` takes one name or a comma-separated list; the server exposes the union and
fails closed on unknown or empty names.

- `discovery`: read-only — `redmine_search_issues`, `redmine_get_issue`, `redmine_list_projects`,
  `redmine_health`, `redmine_get_project_metadata`, `redmine_validate_template`.
- `issue`: lookups plus bug/comment preview and write.
- `metadata`: project metadata and template validation.
- `integration`: reserved for the qa-integration-agent child process.
- `all`: every tool.

Presets shared with testlink-mcp: `batch-import` → `discovery` (bugs are filed through
qa-integration-agent), `manual-qa` → `issue`, `bug-tracking` → `discovery`.

When the variable is unset the server exposes only `discovery`. Releases up to v1.9.0 defaulted
to `all`, so a registration without the variable could create bugs and comments.

## Validation

All automated tests must use fake clients or local mock HTTP endpoints. CI must never call the corporate Redmine/eITS service.
