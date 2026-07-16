# Multi-Agent MCP Migration

## Decision

The current package remains the compatibility source while responsibilities are separated into:

1. `testlink-mcp`: TestLink-only adapter and operation protection.
2. `redmine-mcp`: corporate Redmine/eITS-only adapter and operation protection.
3. `qa-integration-agent`: report parsing, orchestration, cross-system dedupe, traceability, audit, and resume.
4. `qa-mcp-contracts`: versioned structured handoff schemas.

The upstream TestLink and Redmine systems are maintained by different teams. The MCP implementations are maintained by the same integration owner, but must have separate credentials, releases, health checks, and failure domains.

## Non-Negotiable Invariants

- Every external write defaults to preview.
- A write must match a previously reviewed `preview_digest`.
- `corp` and `sandbox` are explicit and must never be mixed.
- Fail/Error Redmine creation is opt-in and deduplicated.
- Closed issues are never reopened automatically.
- Status, assignee, fixed version, and issue closure are not changed automatically.
- TestLink and Redmine credentials remain inside their respective MCP servers.
- Every write produces an operation audit safe for retry.
- All automated tests run offline.

## Baseline

- Package version: `1.3.0`
- Baseline commit: `9f05ce4`
- MCP tools: 33
- Offline tests: 104 passing
- Baseline command: `python -m unittest discover -s tests`

## Implementation Status

- Phase 0 complete: baseline frozen and verified offline.
- Phase 1 complete: v1 contracts and digest guard implemented.
- Phase 2 complete in the compatibility repository: standalone `redmine-mcp` server and protected tools implemented.
- Phase 3 complete: pure `testlink-mcp` v2 boundary and protected execution path implemented.
- Phase 4 complete: `qa-integration-agent` preview/execute/resume/audit/traceability path implemented.
- Phase 5 implementation complete: shadow comparison tool, cutover gates, and rollback runbook added. Live sandbox/corporate execution remains an explicitly approved deployment activity.

## Migration Phases

### Phase 1: Contracts

- Define operation, preview, result, error, and audit schemas.
- Add schema safety and compatibility tests.
- Freeze v1 field meanings after review.

### Phase 2: Redmine MCP

- Extract REST transport, project templates, custom fields, dedupe, create, and comment operations.
- Enforce preview digest and environment policy at the server boundary.
- Add offline HTTP contract tests and audit tests.

### Phase 3: TestLink MCP

- Remove Redmine configuration and payload concerns.
- Keep TestLink discovery and execution operations.
- Enforce exact platform/build resolution and preview digest at the server boundary.

### Phase 4: QA Coordinator

- Move `legacy-web-ems-report-v1` parsing and upload orchestration into the coordinator.
- Implement item-level state transitions, aggregated preview, confirmation, traceability validation, and resume.
- Keep the old `testlink_upload_report` as a compatibility wrapper for one major release.

### Phase 5: Validation And Cutover

- Compare old and new previews in shadow mode with no duplicate writes.
- Complete sandbox partial-failure and concurrency tests.
- Pilot one approved corporate project/plan before general cutover.

## Acceptance Gates

- Preview causes zero external writes.
- Retry creates zero duplicate Redmine issues and zero duplicate TestLink executions.
- Missing or changed preview digest blocks the write.
- Unknown report schema and unresolved platform/build fail before writes.
- All MCP responses, errors, logs, and audits redact known secret keys and values.
- TestLink notes and Redmine evidence contain the same operation and dedupe identity.
