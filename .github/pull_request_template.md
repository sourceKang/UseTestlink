## Summary

Describe the ownership boundary, workflow, contract, or adapter behavior changed.

## Evidence

Paste real output from this branch, not a plan. Show before and after.

- **Before:** <failing test run, old tool output, or old preview artifact>
  **After:** <passing test run, new tool output, or new preview artifact>

## Merge Danger

**Door:** <one-way or two-way>

<One-way: hard to roll back, e.g. corporate write path, Redmine create, MCP tool schema or contract consumed by clients, audit/resume format. Two-way: offline-only, docs, tests.>

**Blast Radius:** <testlink-mcp / redmine-mcp / qa-integration-agent / offline-only>

<Optional: who or what breaks if this is wrong.>

## Safety impact

- [ ] Preview remains the default for every write-capable path.
- [ ] Write requires explicit confirmation and a matching preview digest.
- [ ] `corp` / `sandbox` guard is enforced at the server boundary.
- [ ] Redmine create remains explicit opt-in with dedupe/reuse.
- [ ] No automatic issue status, closure, assignee, or fixed-version change was added.
- [ ] Partial failure is auditable, resumable, and idempotent.
- [ ] TestLink/Redmine credentials stay inside their respective MCP server.
- [ ] Errors, responses, logs, and audits redact secrets.

## Validation

- [ ] `python -m unittest discover -s tests`
- [ ] Contract/schema validation
- [ ] Preview performs zero external writes
- [ ] Digest/environment/target mismatch tests fail closed
- [ ] Retry/resume duplication tests
- [ ] Traceability validation
- [ ] `git diff --check`

## Deployment / rollback

State whether this is offline-only, shadow, sandbox, or an explicitly approved corporate pilot. Describe a non-destructive rollback path.

## Repository hygiene

- [ ] No `.env`, `local/`, downloaded report, API key, devKey, token, or password is included.
- [ ] Relevant architecture, workflow, field-policy, and runbook docs are updated.
