# Protected Testcase Maintenance

Use this procedure for TestLink testcase creation, updates, or requests to verify testcase
step formatting.

## Standard Flow

1. Verify that the active `testlink-mcp` comes from an approved tagged installation and has
   a TestLink-only credential path.
2. Discover the exact project and suite. For updates, read the current testcase and resolve
   its exact external/internal ID and version.
3. Prepare non-empty logical pairs in source order: `action => expected`.
4. Call `testlink_create_testcase` or `testlink_update_testcase` without write. Keep
   `single_step=true` and `allow_multi_row=false` for the normal path.
5. Confirm that preview reports `planned_row_count=1`, matching logical-step count, the
   intended target/content, and no duplicate block. Present the `preview_digest`.
6. Wait for explicit confirmation of the environment, target, content, and unchanged digest.
7. Call the same tool with unchanged inputs, `write=true`, and the reviewed digest.
8. Require `verification_status=verified` and record the MCP audit ID. Use the read-only
   testcase lookup when the user asks for an additional independent verification.
9. Produce the verification report below. Never claim success from the XML-RPC response
   alone.

Do not call legacy `create_test_case`, `update_test_case`, `testlink-agent-mcp`, or a CLI
fallback merely to avoid the protected contract.

## Row Policy

Normal testcase content is one TestLink row containing numbered Actions and matching
Expected lines. Reject empty expected results, different numbering, or more than one row.

Multiple rows are exceptional. Use them only when the user explicitly requests them for the
exact payload under review. Send both `single_step=false` and `allow_multi_row=true`; either
flag alone must fail. Changing either flag invalidates the preview digest and requires a new
preview and confirmation.

## Verification Failure

If the MCP returns `verification_failed`, `indeterminate`, or a readback mismatch:

- report that TestLink may have accepted a write but verification failed;
- cite the audit ID and mismatch fields without exposing secrets;
- do not overwrite, delete, recreate, or declare success;
- resume only with the same operation identity and audit evidence so the MCP can read back
  before deciding whether any write is still missing.

## Verification Report

Report:

```text
Environment:
Project / Suite:
Testcase ID / External ID / Version:
Operation ID:
Mode: preview_only | write
Row policy:
Logical step count:
TestLink row count:
Preview digest:
Verification: preview_only | verified | verification_failed
Expected digest / Readback digest:
Mismatch fields:
Audit ID:
```
