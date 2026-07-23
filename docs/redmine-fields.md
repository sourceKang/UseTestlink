# Redmine/eITS Field Policy

This document defines Redmine/eITS field handling for `testlink-agent` and the standalone `redmine-mcp` boundary.

Formal bugs must be created in the corporate Redmine/eITS workflow. A local Redmine, if deployed, is a sandbox only.

## Ownership Boundary

- `redmine-mcp` alone loads `REDMINE_API_KEY` and calls the Redmine REST API.
- `qa-integration-agent` sends validated issue/comment intent and traceability context, never credentials.
- `testlink-mcp` receives only the resulting Redmine ID/URL needed in execution notes.
- Formal work does not fall back to Chrome/browser control when the Redmine MCP is missing; fix or explicitly configure the MCP credential path.
- Metadata discovery and template validation are read-only. Issue and comment writes require a matching preview digest and produce audit JSON.

## Required Target Configuration

Environment variables:

```text
REDMINE_URL=<corporate Redmine URL or sandbox URL>
REDMINE_API_KEY=<personal Redmine API key>
REDMINE_PROJECT_ID=<corporate project identifier>
REDMINE_TEMPLATE=<local/redmine_templates/*.json>
REDMINE_TRACKER_ID=<tracker id>
REDMINE_PRIORITY_ID=<priority id>
REDMINE_ENV=corp|sandbox
REDMINE_MCP_ENV_FILE=<absolute or repository-local env file path>
```

`REDMINE_ENV=corp` means formal company workflow. `REDMINE_ENV=sandbox` means development-only Redmine.

## Manager-Only Fields

These fields are restricted by default:

```text
assigned_to_id
fixed_version_id
```

Additional company-controlled fields should be added to this list when identified.

The only supported override is:

```text
REDMINE_ALLOW_MANAGER_FIELDS=true
```

This switch may only be set on a manager-owned machine or explicitly approved environment. It must not be committed to shared env files.

## Minimum Issue Payload

Created Redmine issues must include:

```text
project_id
subject
description
tracker_id
priority_id
custom_fields, when required by the corporate tracker
```

The issue description must include TestLink evidence:

```text
TestLink Project:
Test Plan:
Platform:
Build:
Test Case:
Test Case Name:
Automation Test Function:
Result:
Report File:
Execution URL:
Dedupe Key:
```

## Image Attachment Policy

Bug creation may include optional image evidence through `redmine-mcp`. Attachment input is
local file intent only; API credentials and Redmine upload tokens are never MCP arguments.

- Preview computes and displays filename, detected MIME type, byte size, and SHA-256.
- The exact attachment metadata and content digest are included in `preview_digest`.
- Upload occurs only with `write: true` and the matching digest, after the final dedupe check.
- Supported formats are PNG, JPEG, GIF, WebP, and BMP; maximum five images and 10 MiB each.
- Upload tokens are internal ephemeral values and must not be written to responses, errors, or audit JSON.
- When an open dedupe match is reused, create-bug does not upload the supplied images and reports a warning.
- A failed upload is audited and the issue create call is not made; retry requires the unchanged confirmed input.

## Custom Field Template

Template files live under:

```text
local/redmine_templates/
```

Example:

```text
local/redmine_templates/corp-redmine.json
local/redmine_templates/sandbox-redmine.json
```

Shared examples live in:

```text
docs/redmine-template.example.json
```

Custom fields must use Redmine field IDs, not only display names. Display names are useful for review, but IDs are required for API writes.

## Release Note Format

Formal release note entries use:

```text
[Bug #<Redmine ID>] <Ticket Subject>
```

If an item also has eITS#, the RM# must appear before eITS#:

```text
[Bug #12345] <Ticket Subject> eITS#67890
```

This ordering is required so PQA import can automatically map the RM# back to TestLink.

## Dedupe Metadata

The agent must compute a dedupe key for Fail/Error results before creating an issue.

The dedupe key must be stored or rendered in a way that supports later lookup. Initial implementation may place it in the Redmine description/comment as:

```text
Dedupe Key: <value or digest>
```

If the corporate Redmine tracker has a dedicated custom field for automation dedupe, prefer that field once the ID is confirmed.

## Reuse And Comment Rules

When an existing open issue matches the dedupe key:

- Do not create a new issue.
- Reuse the issue.
- Add build/report/TestLink evidence as a comment when supported.
- Record `REDMINE-REUSED: yes` in TestLink execution notes.

When no matching issue exists:

- Create a new issue only if Redmine creation was explicitly requested.
- Record `REDMINE-REUSED: no` in TestLink execution notes.

When a closed issue matches:

- Do not reopen it automatically.
- Produce a preview warning.
- Let the user decide whether to link existing issue, create a new issue, or ask the owner to reopen.

## Field Discovery Checklist

Before using a new corporate tracker, confirm:

- Redmine project identifier
- Tracker ID
- Priority ID mapping
- Required custom field IDs
- Field allowed values
- Which fields are manager-only
- Whether a dedupe custom field exists
- Whether comments can be added through API
- Whether issue search can filter by custom field or must search open issues by text

Document the confirmed values in a local template file under `local/redmine_templates/`; do not commit company secrets or personal API keys.
