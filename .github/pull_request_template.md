## Summary

Describe the ownership boundary, workflow, contract, or adapter behavior changed.

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
