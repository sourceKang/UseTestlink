# TestLink Agent Architecture

`testlink-agent` provides the `testlink-mcp` server for agent-safe TestLink operations and controlled Redmine/eITS integration.

The project is a QA integration layer. It does not replace TestLink, and it does not create a second formal Redmine workflow.

## System Roles

```text
Automation Report
        |
        v
testlink-agent / testlink-mcp
        |
        +--> TestLink
        |
        +--> Corporate Redmine / eITS
```

## Source Of Truth

| Domain | Source of truth | Notes |
|---|---|---|
| Test project, test plan, platform, build | TestLink | `testlink-agent` reads and writes through XML-RPC. |
| Test case and execution result | TestLink | Execution notes may include Redmine references. |
| Bug, RM#, eITS#, assignment, status, fixed version | Corporate Redmine/eITS | Formal defects must use the company system. |
| Automation report import decision | `testlink-agent` preview + user confirmation | Write operations are opt-in. |
| Operation evidence | `local/audit/*.json` | Local audit files are ignored by git. |

## Deployment Boundaries

TestLink and Redmine/eITS must stay separate:

- Separate services
- Separate databases
- Separate backup and restore process
- Separate user roles and permissions
- API-based integration only

If a local Redmine is deployed later, it must be named and documented as a sandbox. It must not be used for formal RM#, eITS#, release note, or PQA import flow.

The optional `infra/redmine-sandbox/` compose setup is development-only. It is not a production Redmine recipe and must not be connected to formal TestLink/PQA release-note workflows.

## Environment Profiles

Every write-capable run should know which environment is being targeted:

```text
TESTLINK_AGENT_PROFILE=corp
REDMINE_ENV=corp
```

or:

```text
TESTLINK_AGENT_PROFILE=sandbox
REDMINE_ENV=sandbox
```

`corp` means the corporate TestLink and corporate Redmine/eITS flow. `sandbox` means local or development-only systems. Agents must not infer that a sandbox Redmine is formal.

## Write Safety Model

All write paths follow this model:

```text
parse input
  -> validate schema and target profile
  -> resolve TestLink target
  -> compute dedupe key for Fail/Error results
  -> preview TestLink and Redmine actions
  -> wait for explicit confirmation
  -> write
  -> record audit log
```

Write-capable commands default to preview. Redmine bug creation is also opt-in. Destructive operations require extra confirmation.

## Key Modules

| Module | Responsibility |
|---|---|
| `testlink_agent_core.cli` | CLI arguments and command routing. |
| `testlink_agent_core.commands` | CLI command orchestration. |
| `testlink_agent_core.mcp_server` | MCP server entrypoint and tool exposure. |
| `testlink_agent_core.client` / `clients` | TestLink XML-RPC access. |
| `testlink_agent_core.redmine` | Redmine API access and issue payload construction. |
| `testlink_agent_core.reports` | Automation report parsing. |
| `testlink_agent_core.policy` | Target environment, allowed fields, dedupe, and idempotency rules. |
| `testlink_agent_core.audit` | Write audit records and retry evidence. |
| `testlink_agent_core.output` | Console/MCP output and sensitive value masking. |

`policy.py` and `audit.py` are planned modules. Add them before expanding write behavior.

## Future Structure

Do not move modules only for aesthetics. After policy and audit behavior are stable, the core can be split into:

```text
testlink_agent_core/
  integrations/
    testlink_client.py
    redmine_client.py
  reports/
    parser.py
    schema.py
  policy.py
  audit.py
```

The migration must preserve CLI and MCP behavior and keep offline tests passing.

## Non-Goals

- Do not store formal Redmine data in TestLink-only files.
- Do not use local Redmine as a formal defect system.
- Do not auto-close Redmine issues.
- Do not auto-assign issues or set fixed versions unless a manager-owned environment explicitly allows it.
- Do not expose API keys, devKeys, tokens, or passwords in logs or MCP responses.
