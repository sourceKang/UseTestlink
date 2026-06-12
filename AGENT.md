# Agent Instructions For TestLink Agent CLI

You are operating TestLink through `testlink_agent.py`.

## Credentials

- Read TestLink URL from `TESTLINK_URL`.
- Read personal API key from `TESTLINK_DEVKEY`.
- Never print or commit the devKey. Mask it as `*****` in conversation.

## Normal Workflow

1. Run preview first.
2. Confirm project, test plan, platform, build, and result counts.
3. Use `--write` only after preview has no missing or duplicate test cases.
4. Report success and failure counts back to the user.

## Commands

Preview:

```powershell
python .\testlink_agent.py upload-report --project "NetAtlasEMS" --plan "NetAtlas EMS" --platform "NetAtlas EMS" --build-id "19641" --report "D:\path\report.txt" --skip-policy ignore
```

Write:

```powershell
python .\testlink_agent.py upload-report --project "NetAtlasEMS" --plan "NetAtlas EMS" --platform "NetAtlas EMS" --build-id "19641" --report "D:\path\report.txt" --skip-policy ignore --write
```

## Bug Handling

Department bug integration is not configured yet. Do not call native TestLink bug parameters or invent an internal API. If a user provides bug IDs, record them in execution notes as:

```text
BUG-ID: <bug id>
```

## Release Note Content

The department `Release Note Content` page is a custom TestLink UI feature. Native XML-RPC calls such as build lookup and test suite lookup do not expose this content today. Do not claim release notes are absent only because XML-RPC cannot see them. Tell the user that this integration is pending TestLink owner guidance, then update the tool when the custom API, database table, or supported access method is provided.

## Safety

- Do not use overwrite unless a future version explicitly implements an approval-protected option.
- Do not delete executions.
- Only operate on the user-specified project, test plan, platform, and build.
- Preserve TestLink XML-RPC fault codes and messages when an API call fails.
