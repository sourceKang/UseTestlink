# Agent Token Budget

`scripts/token_budget_report.py` provides a deterministic regression estimate. It serializes
the advertised MCP tool schemas compactly and divides text characters by four. This is not
a tokenizer bill; it is a stable comparison metric for this repository.

Run:

```powershell
python scripts/token_budget_report.py
```

## 1.6.0 Baseline And Result

| Surface | Before | After | Reduction |
|---|---:|---:|---:|
| Persistent MCP schema for integrated report work | ~9,921 tokens (all three servers) | ~1,116 tokens (`qa-integration-agent`, `import`) | 88.8% |
| `AGENTS.md` | ~1,207 tokens | ~336 tokens | 72.2% |
| Router `testlink-agent/SKILL.md` | ~1,371 tokens | ~166 tokens | 87.9% |

Direct task profiles remain below 2,200 estimated schema tokens: TestLink execution ~1,739,
testcase maintenance ~2,174, and Redmine issue work ~2,128. The budget test requires every
task profile to stay at least 70% smaller than the all-server surface.

## Runtime Controls

- Default Codex registration exposes only QA import tools.
- Direct work enables one of `TESTLINK_MCP_TOOLSET`, `REDMINE_MCP_TOOLSET`, or
  `QA_INTEGRATION_TOOLSET`; disabled tools are absent from `tools/list` and rejected if called.
- `testlink_resolve_execution_target` resolves the exact execution target in one MCP call.
- `qa_preview_report_artifact` persists the complete redacted plan/review artifact and returns a bounded
  summary. `qa_execute_preview_artifact` / `qa_resume_preview_artifact` send artifact
  reference plus digest instead of the full plan; v1 preview/execute/resume tools remain in `legacy`/`all` only.
- Operation lookup is compact by default; full audit details require explicit opt-in.

## Safety Budget

Token reduction must not remove preview-first behavior, exact target validation, digest
binding, Redmine dedupe/reuse, report hash validation, write confirmation, audit/resume,
readback verification, secret redaction, or offline tests. These are correctness contracts,
not optional prompt text.
