# Multi-Agent Cutover Runbook

## Purpose

Move integrated report imports from the legacy combined server to `qa-integration-agent` + `testlink-mcp` + `redmine-mcp` without duplicate TestLink executions or Redmine issues.

No step in this runbook authorizes a corporate write by itself. Preview, sandbox write, and corporate pilot confirmations are separate approvals.

## Service Configuration

Keep credentials separate:

```text
testlink-mcp
  TESTLINK_MCP_ENV_FILE=local/testlink_mcp.env
  TESTLINK_URL, TESTLINK_DEVKEY, TESTLINK_AGENT_PROFILE

redmine-mcp
  REDMINE_MCP_ENV_FILE=local/redmine_mcp.env
  REDMINE_URL, REDMINE_API_KEY, REDMINE_ENV

qa-integration-agent
  no TestLink or Redmine API credentials
```

Do not copy credentials into MCP arguments, preview JSON, audit JSON, or shared configuration. Confirm that all services agree on `corp` or `sandbox` before any write.

To migrate an existing combined local env without printing secret values, use the allowlist-based helper. It intentionally excludes manager-only fields:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\split_mcp_env.ps1 `
  -TestLinkSource local\testlink_agent.env `
  -RedmineSource .env `
  -Environment corp
```

The generated files remain under ignored `local/`. Use separate sandbox source files and `-Environment sandbox` for Gate 3; never relabel corporate credentials as sandbox.

## Gate 1: Offline Release Validation

Required:

- `python -m unittest discover -s tests` passes without network access.
- Contract JSON parses and v1 compatibility tests pass.
- Preview digest mismatch, missing target, environment mismatch, closed Redmine match, and manager-only fields all fail closed.
- Secret keys and known secret values are redacted from responses, exceptions, and audit JSON.
- Packaging exposes `testlink-mcp`, `redmine-mcp`, and `qa-integration-agent-mcp`.

Failure at this gate blocks deployment.

## Gate 2: Read-Only Shadow

For representative reports:

1. Produce a legacy `testlink_upload_report` preview with `write: false`.
2. Produce a modern `qa_preview_report_import` preview using the exact same target and options.
3. Save both redacted JSON results locally.
4. Run `qa_compare_shadow_previews`.
5. Review target, write/ignored counts, failed testcase IDs, and Redmine create/reuse decisions.

Acceptance:

- Zero external writes.
- Exact project/plan/platform/build match.
- Same parsed and ignored result population.
- Same failure population.
- A legacy `create-or-reuse` may match the modern concrete `create` or `reuse` decision; other decision differences block cutover.

## Gate 3: Sandbox Write And Failure Injection

After explicit sandbox confirmation, validate one unique operation at a time:

- Normal Pass and Fail/Error import.
- Existing-open Redmine issue reuse.
- Closed issue match blocks automatic reopen/create.
- Redmine succeeds, TestLink fails, then resume reuses the issue and writes TestLink once.
- TestLink succeeds, evidence comment fails, then resume skips TestLink and retries only the comment.
- Process interruption after a `started` service audit recovers from remote evidence or fails closed when evidence is indeterminate.
- Replaying a successful operation returns skipped/resumed semantics and causes no duplicate writes.
- Concurrent attempts with the same dedupe/operation identity do not create duplicates.

Acceptance:

- One Redmine issue maximum per dedupe key.
- One TestLink execution maximum per protected operation.
- Traceability validator passes.
- Service and workflow audits contain no credentials.

## Gate 4: Corporate Pilot

Prerequisites:

- TestLink owner confirms project, plan, platform, build, and execution behavior.
- Redmine/eITS owner confirms project template, tracker, priority, required custom fields, dedupe search behavior, and comment permission.
- A named approver reviews the aggregate preview and digest.
- Pilot is limited to one approved project/plan/report and a unique operation ID.

Run preview first. Corporate write requires a new explicit confirmation that names the target, counts, Redmine actions, and preview digest. After writing, verify both systems and the three audit layers before expanding scope.

## Rollback

Rollback means stop routing new integrated work through the coordinator and return to the legacy compatibility preview path. It does not mean deleting TestLink executions or Redmine issues.

- Disable the coordinator entrypoint for new work.
- Preserve all service/workflow audits and operation IDs.
- Complete or manually reconcile in-flight partial failures; never rerun them under a new identity without review.
- Keep pure MCP read-only tools available for diagnosis.
- Do not close, delete, overwrite, reassign, or change fixed versions as cleanup.

## Cutover Completion Criteria

- At least one representative shadow set has zero blocking differences.
- Approved sandbox failure-injection cases pass.
- Corporate pilot traceability and audit review pass.
- The legacy combined entrypoint is documented as compatibility-only.
- Owners and rollback contact are recorded outside this repository in the team's operational system.
