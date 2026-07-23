---
name: testlink-agent
description: Use this skill for the testlink-agent repository or its TestLink, Redmine/eITS, and QA coordinator MCP workflows, including discovery, protected writes, report import, dedupe, traceability, audit, and resume.
---

# TestLink Agent

## Core Rule

Route work by ownership boundary:

- Cross-system automation report import: `qa-integration-agent`.
- TestLink-only discovery or execution: `testlink-mcp`.
- Redmine/eITS-only metadata, issue, or comment work: `redmine-mcp`.
- `testlink-agent-mcp` and `testlink_upload_report` are migration compatibility paths only.

Never perform an external write first. Review the exact preview and its `preview_digest`, then write only after the user explicitly confirms the environment, target, and payload. Never reuse a digest after inputs change.

## Setup Check

For MCPs shared across Codex projects, verify that the executables come from the GitHub-installed user tool environment rather than an editable `D:\UseTestlink` checkout. Follow `docs/deployment.md` for install, upgrade, source verification, and rollback. The repository-local `.codex/config.toml` is for development only.

Verify the relevant server has its own credential path before calling live tools:

- TestLink: `TESTLINK_URL` + `TESTLINK_DEVKEY`, normally through `TESTLINK_MCP_ENV_FILE`.
- Redmine: `REDMINE_URL` + `REDMINE_API_KEY` + explicit `REDMINE_ENV`, normally through `REDMINE_MCP_ENV_FILE`.
- Legacy compatibility only: `TESTLINK_AGENT_ENV_FILE` or `local/testlink_agent.env`.

Do not pass credentials through MCP tool arguments. Do not silently switch formal Redmine work to browser/Chrome when the MCP is missing or misconfigured; report the configuration blocker instead. Never display `TESTLINK_DEVKEY`, `REDMINE_API_KEY`, or other secrets.

## Integrated Report Workflow

1. Discover the exact target with TestLink read-only tools:
   - `testlink_list_projects`
   - `testlink_list_plans`
   - `testlink_list_platforms`
   - `testlink_list_builds`
   - `testlink_list_suites`
   - `testlink_find_suites`
2. Call `qa_preview_report_import` with an explicit `operation_id`, `environment`, project, plan, platform, build, and report.
3. Summarize target, counts, ignored rows, failures, Redmine create/reuse decisions, warnings, and `preview_digest`.
4. After explicit confirmation, call `qa_execute_report_import` with unchanged inputs, the matching digest, and `write: true`.
5. Validate the workflow audit and traceability.
6. For partial failure, use `qa_resume_report_import` with the same operation identity and matching audit. Do not rerun the whole import as a new operation.

## Safety Notes

- The coordinator requires an exact platform and build; it never substitutes another platform because the requested one is absent.
- Pure `testlink-mcp` excludes Redmine operations and legacy combined report upload.
- `redmine-mcp` enforces dedupe: an open match is reused and a closed match blocks automatic creation/reopen.
- `skip_policy: "ignore"` leaves skipped rows out of TestLink writes; `skip_policy: "blocked"` writes skipped rows as blocked.
- Redmine bug creation is opt-in with `redmine_create_bugs: true`.
- When a Redmine project requires custom fields, pass `redmine_template` and preview first. The template is project-specific and should define required custom fields before `write: true`.
- The agent may create issue descriptions or evidence comments, but may not close issues or change status, assignee, or fixed version.
- Local files such as catalogs and profiles are under `local/` by default and are ignored by git.

## Direct MCP Work

For a TestLink-only write, use the protected `testlink-mcp` preview/result tools and a matching digest. For a Redmine-only write, use `redmine_preview_bug` or `redmine_preview_comment`, then the corresponding write tool only after confirmation.

For image evidence on a new Redmine bug, pass `attachments` to `redmine_preview_bug` as
objects containing `file` and optional `filename`/`description`. Review every returned
filename, MIME type, size, SHA-256, attachment action, warning, and the final
`preview_digest`. Call `redmine_create_bug` with the unchanged files only after explicit
confirmation. Supported images are PNG, JPEG, GIF, WebP, and BMP, with at most five images
and 10 MiB per image. Upload tokens are internal to `redmine-mcp` and must never be requested,
logged, or passed between agents. If dedupe reuses an open issue, create-bug deliberately
does not upload the images; report the `not-uploaded-reused` result instead of implying that
evidence was attached.

Before creating or updating a testcase, read
[`references/testcase-maintenance.md`](references/testcase-maintenance.md) and follow its
single-row preview, explicit multi-row authorization, readback verification, and reporting
procedure. Use only `testlink_create_testcase` or `testlink_update_testcase` on the pure
server; do not use legacy testcase write aliases as a bypass.

## CLI Fallback

Use the legacy CLI only when MCP is unavailable or the user explicitly requests terminal commands:

```powershell
python .\testlink_agent.py list-projects
python .\testlink_agent.py list-plans --project "Project"
python .\testlink_agent.py upload-report --project "Project" --plan "Plan" --platform "Platform" --report "reports\report.txt"
```

Add `--write` only after the preview has been reviewed and confirmed. The fallback does not change the ownership or safety rules above.
