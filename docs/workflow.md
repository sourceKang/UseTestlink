# TestLink Agent Workflow

## v2 Recommended Flow

The recommended integrated path is `qa-integration-agent` -> ownership-specific MCP servers. The legacy combined upload remains a compatibility path only.

```text
1. Assign stable operation_id and explicit corp|sandbox environment
2. Parse and strictly validate legacy-web-ems-report-v1
3. Resolve exact TestLink project/plan/platform/build
4. Ask redmine-mcp for dedupe-aware bug previews when explicitly enabled
5. Ask testlink-mcp for execution previews
6. Persist the exact plan/review artifact and return a compact aggregate preview plus preview_digest; perform zero writes
7. Review the artifact and wait for explicit confirmation of its unchanged digest
8. Execute by preview_artifact reference; verify the report hash before any external write
9. Write TestLink execution with Redmine traceability
10. If an issue was reused, append Redmine evidence after TestLink succeeds
11. Save service audits and the item-level workflow audit
12. Validate traceability; resume only missing actions after partial failure
```

The platform and build are exact inputs. A missing requested platform is a target validation failure; the coordinator must not replace it with another existing platform without a new user-selected preview.

## Confirmation Contract

- `qa_preview_report_artifact` is read-only, persists the exact redacted plan/review under `local/`, and returns its path plus canonical `preview_digest`.
- The conversational response is bounded: aggregate counts, warnings, target, a short testcase sample, artifact path, and digest. Exact per-item payloads stay in the review artifact.
- `qa_execute_preview_artifact` requires `write: true`, the same `operation_id`, the returned `preview_artifact`, and the matching digest. The coordinator loads that plan instead of rebuilding it from repeated arguments.
- Any changed report, target, template, custom field, or Redmine opt-in invalidates the digest and requires a new preview.
- `qa_resume_preview_artifact` uses the same preview artifact plus prior audit identity and completed item states; it is not a fresh bulk retry.

## Protected Testcase Maintenance

Use `testlink_create_testcase` and `testlink_update_testcase` for formal MCP testcase
maintenance. Do not call the legacy `create_test_case` or `update_test_case` names through
the pure server.

```text
1. Discover the exact project, suite, testcase, and version
2. Read the current testcase before an update
3. Supply logical action/expected pairs
4. Preview with single_step=true (default)
5. Review the one-row payload, target, row counts, and preview_digest
6. After explicit confirmation, write with unchanged inputs and digest
7. Read the testcase back from TestLink
8. Compare normalized row count and every written field
9. Return success only when readback matches; otherwise record verification_failed
```

Multi-row output is exceptional and requires both `single_step=false` and
`allow_multi_row=true`. Either value alone is rejected. The authorization is part of the
preview digest and cannot be reused after steps, target, environment, or row policy change.

For a default single-row write, Actions and Expected must contain the same non-empty,
contiguous logical step numbers. A successful XML-RPC response without matching readback is
an indeterminate/partial operation, not a success. Resume must inspect the prior audit and
readback before any retry so testcase creation is not duplicated.

This document defines the expected workflow for importing automation results into TestLink and linking Fail/Error results to corporate Redmine/eITS.

## Normal Upload Flow

```text
1. Load environment profile
2. Parse automation report
3. Validate report schema
4. Resolve TestLink project, plan, platform, and build
5. Map report entries to TestLink test cases
6. Compute Redmine dedupe keys for Fail/Error entries
7. Query Redmine for existing open issues
8. Produce preview
9. Wait for explicit user confirmation
10. Create or reuse explicitly enabled Redmine issues for Fail/Error
11. Write TestLink execution results with traceability
12. Write bidirectional traceability
13. Save local audit log
```

## Preview Requirements

Preview output must show:

- Target profile: `corp` or `sandbox`
- Report schema version
- TestLink project, plan, platform, build
- Number of parsed results
- Missing TestLink test cases
- Planned TestLink result writes
- For each Fail/Error result, whether Redmine will create or reuse an issue
- Dedupe key or a stable short digest of it
- Whether manager-only Redmine fields are blocked or enabled
- Resolved Severity label and built-in `priority_id`
- The distinct custom Priority field ID/value, including an explicit blank value
- The review artifact path containing each exact safe Redmine issue payload covered by the preview digest

Preview output must not contain API keys, devKeys, tokens, or passwords.

After a new Redmine issue is created, the service must read back and independently compare
the built-in Severity transport (`issue.priority.id`) and configured custom Priority field.
Verification failure is a partial operation and must retain the created issue identity in
audit so retry/recovery cannot create a duplicate.

## Report Schema Validation

The current supported automation report schema is:

```text
legacy-web-ems-report-v1
```

The parser must fail fast when:

- The report is not valid UTF-8.
- The report does not contain `Test Results:`.
- The report does not contain at least one recognized TestLink result row.

Recognized legacy result rows use:

```text
[<External ID>][<Automation Test Function>] Result <Pass|Fail|Blocked|Skip|Skipped|Error> (<duration>)
```

Unknown or changed report formats must be rejected instead of guessed. Add a new schema version and tests before accepting a new format.

## Redmine Dedupe Flow

Fail/Error results must be deduplicated before creating issues.

```text
dedupe_key =
  redmine_project_id
  testlink_project
  testlink_plan
  platform
  testcase_external_id
  failure_signature
```

Initial `failure_signature`:

```text
test_name + raw_status + normalized failure summary
```

If no detailed failure summary exists, use the normalized test function and raw result. When report schema adds error messages or stack traces, include a normalized excerpt or hash in the signature.

Decision rules:

```text
existing open issue found -> reuse and append evidence
no matching open issue    -> create new issue
closed issue found        -> do not silently reopen; create preview warning
```

## Bidirectional Traceability

TestLink execution notes must include:

```text
REDMINE-ID: #<issue id>
REDMINE-URL: <issue url>
REDMINE-REUSED: yes/no
```

Redmine issue description or comment must include:

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

If a TestLink execution URL is not available through XML-RPC, record the TestLink project/plan/platform/build/testcase values and leave `Execution URL:` blank or mark it unavailable.

## Retest Flow

When an issue already exists:

```text
retest fail  -> after TestLink write succeeds, add Redmine comment with new build/result/TestLink evidence
retest pass  -> after TestLink write succeeds, add Redmine comment with pass evidence
```

The agent must not automatically:

- Close the issue
- Change issue status
- Change assignee
- Change fixed version
- Change target release

Those state transitions remain human workflow decisions in corporate Redmine/eITS.

If the TestLink write succeeds but the Redmine evidence comment fails, the operation is a partial failure. The audit log must keep the TestLink success and the Redmine comment error so retry can add the missing evidence without losing the execution record.

## Partial Failure Recovery

Every write operation must produce an audit log. Partial failure handling:

| Situation | Required behavior |
|---|---|
| TestLink write succeeds, Redmine write fails | Record TestLink success and Redmine error; retry must not duplicate TestLink work. |
| Redmine issue is created, TestLink write fails | Record Redmine issue ID; retry must reuse existing Redmine issue. |
| Both fail | Record input hash, target profile, and errors. |
| Process is interrupted | Audit log must preserve completed item-level actions when possible. |

Retries must use dedupe and audit evidence. Do not rely on human memory to prevent duplicate issues.

## Resume Flow

`upload-report` supports explicit resume with:

```text
--resume-audit <audit.json>
```

Resume is accepted only when the previous audit matches the current run:

- Operation
- Report SHA-256
- Report schema
- TestLink/Redmine profile
- TestLink target

When a previous audit item has `testlink_write: success`, the retry skips the TestLink write for that testcase. If that same item has `redmine_comment: failed`, the retry attempts only the missing Redmine evidence comment after reconstructing the linked issue from the audit record.

When a previous audit item has a Redmine issue but `testlink_write: failed`, the retry reuses the recorded Redmine issue instead of creating another one, then attempts the TestLink write again.

## Release Note Flow

Corporate release note entries must preserve the Redmine format:

```text
[Bug #<Redmine ID>] <Ticket Subject>
```

When both RM# and eITS# are present, RM#ID must appear before eITS#ID so PQA import can map the item back into TestLink.

## Test Requirements

Tests must run offline. Unit tests must mock:

- TestLink XML-RPC calls
- Redmine REST calls
- Audit log filesystem writes when practical

No test should call the corporate TestLink or corporate Redmine/eITS system.

## Secret Redaction

The agent must redact secrets before data leaves the process or is written to audit files.

Required redaction surfaces:

- MCP `tools/call` content
- JSON-RPC error responses
- CLI/API structured results
- Local audit JSON files
- Normalized exception payloads

Secret keys include:

```text
devKey
api_key
password
token
secret
TESTLINK_DEVKEY
REDMINE_API_KEY
```

Known secret values from `TESTLINK_DEVKEY` and `REDMINE_API_KEY` must also be masked when they appear inside free-form strings such as exception messages, notes, raw responses, or audit payloads.
