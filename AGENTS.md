# TestLink Agent Operating Rules

Assistant responses in this repository should be written in Traditional Chinese.

Repo/package name: `testlink-agent`.
MCP server name: `testlink-mcp`.
Formal project role: QA Integration Agent for TestLink + corporate Redmine/eITS.

This repository is not TestLink itself and is not a second formal Redmine. It is a controlled integration layer between automation reports, TestLink, and the corporate Redmine/eITS workflow.

## System Boundaries

- TestLink is the test record system for projects, plans, platforms, builds, test cases, and execution results.
- Corporate Redmine/eITS is the formal defect workflow for RM#, eITS#, issue status, assignee, fixed version, and release note mapping.
- `testlink-agent` handles preview, schema validation, dedupe, writes, bidirectional traceability, and audit logs.
- Any self-hosted Redmine must be a sandbox only. It must not be used as the formal defect system.

## Write Rules

- All write-capable operations must default to preview.
- Use `--write` or MCP `write: true` only after explicit user confirmation.
- Redmine issue creation is opt-in through `--redmine-create-bugs` or equivalent MCP arguments.
- Destructive operations require explicit confirmation.
- Do not infer deletion, overwrite, issue closure, assignment, or fixed version changes.

## Redmine/eITS Rules

- Formal bugs must use the corporate Redmine/eITS workflow.
- Sandbox Redmine profiles are for local development and testing only.
- Dedupe before creating Redmine issues.
- Reuse existing open issues when the dedupe marker matches.
- After a reused issue receives a successful TestLink execution result, add a Redmine evidence comment.
- The agent may add issue descriptions or comments with evidence.
- The agent must not close issues, change status, change assignee, or change fixed version.
- `assigned_to_id` and `fixed_version_id` are manager-only fields by default.
- `REDMINE_ALLOW_MANAGER_FIELDS=true` may only be used on a manager-owned machine or approved environment.

## TestLink Rules

- TestLink execution notes must include Redmine ID/URL when a Redmine issue is linked.
- Result upload appends execution records by default.
- `overwrite_result` and `delete_execution` are destructive operations and require confirmation.
- TestLink 1.9.16 XML-RPC behavior may be version-dependent; new version assumptions need tests and docs.

## Traceability

Release note Redmine format:

```text
[Bug #<Redmine ID>] <Ticket Subject>
```

When both RM# and eITS# exist, RM# must appear before eITS# so PQA import can map it back to TestLink.

TestLink execution notes should include:

```text
REDMINE-ID: #<issue id>
REDMINE-URL: <issue url>
REDMINE-REUSED: yes/no
Dedupe Key: testlink-agent:<digest>
```

Redmine descriptions or comments should include TestLink project, plan, platform, build, test case, result, report file, and dedupe marker.

## Current Safety Features

- UTF-8 / LF repository defaults through `.editorconfig` and `.gitattributes`.
- `corp` / `sandbox` profile guard through `TESTLINK_AGENT_PROFILE` and `REDMINE_ENV`.
- Report schema fail-fast validation for `legacy-web-ems-report-v1`.
- Dedupe marker generation for Redmine Fail/Error handling.
- Audit JSON for write operations under `local/audit` by default.
- Explicit `--resume-audit` support for retrying partial `upload-report` failures without repeating completed TestLink writes.
- Secret redaction for MCP content, errors, structured results, and audit logs.
- Redmine evidence comments for reused issues after TestLink write success.

## Security Rules

- Never commit `.env`, `local/`, downloaded reports, personal API keys, or devKeys.
- Mask `TESTLINK_DEVKEY`, `REDMINE_API_KEY`, `password`, `token`, and other secret values in logs, MCP responses, errors, and audit logs.
- Tests must run offline and must not call corporate TestLink or corporate Redmine/eITS.
- New write paths must include preview, profile guard, audit logging, retry-safe behavior, and tests.

## Recommended Flow

1. Read `docs/architecture.md`, `docs/workflow.md`, and `docs/redmine-fields.md`.
2. Confirm the active profile is `corp` or `sandbox`.
3. Run preview for write-capable commands.
4. Review target environment, dedupe/reuse decision, and planned writes.
5. Write only after user confirmation.
6. Check `local/audit/` for the generated JSON record.
