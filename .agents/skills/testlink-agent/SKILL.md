---
name: testlink-agent
description: Use this skill when Codex needs to work with this TestLink Agent project or a configured TestLink MCP server: list TestLink projects/plans/platforms/builds/suites, search suite catalogs, manage local TestLink target profiles, download test cases, preview/create/update test cases, preview/upload automation reports, or attach/link Redmine issues through TestLink-safe workflows.
---

# TestLink Agent

## Core Rule

Prefer the MCP tools exposed by `testlink-agent-mcp`. Use the CLI only when MCP is unavailable or when the user explicitly asks for terminal commands.

Never perform external writes first. For `testlink_create_testcase`, `testlink_update_testcase`, and `testlink_upload_report`, call the tool with `write: false` first, review the returned preview, and use `write: true` only after the user explicitly confirms the exact target and payload.

## Setup Check

Verify one credential path exists before calling live TestLink tools:

- Environment variables: `TESTLINK_URL` and `TESTLINK_DEVKEY`
- Or an env file passed as `env_file`
- Or the local default `local/testlink_agent.env`
- For MCP used from another project, prefer `TESTLINK_AGENT_ENV_FILE` with an absolute path. If the current project has no default env file, the agent can fall back to the UseTestlink project root defaults.

Never display `TESTLINK_DEVKEY`, `REDMINE_API_KEY`, or other secrets in responses.

## Workflow

1. Discover the target with read-only tools:
   - `testlink_list_projects`
   - `testlink_list_plans`
   - `testlink_list_platforms`
   - `testlink_list_builds`
   - `testlink_list_suites`
   - `testlink_find_suites`
2. Save frequent suite targets with `testlink_save_profile` when useful.
3. Preview testcase creation, testcase updates, or report upload with `write: false`.
4. Summarize the preview: project, plan, platform, build, suite/profile, counts, ignored rows, failures, and Redmine actions.
5. Ask for confirmation before `write: true`.
6. After writing, summarize success/failure counts and surface any failed rows or TestLink/Redmine errors.

## Important Tool Notes

- `testlink_upload_report` defaults to the latest active/open build when `build` and `build_id` are omitted.
- `skip_policy: "ignore"` leaves skipped rows out of TestLink writes; `skip_policy: "blocked"` writes skipped rows as blocked.
- Redmine bug creation is opt-in with `redmine_create_bugs: true`.
- When a Redmine project requires custom fields, pass `redmine_template` and preview first. The template is project-specific and should define required custom fields before `write: true`.
- Native TestLink `bugid` linking is off by default; use `testlink_bug_link: "notes"` unless the user explicitly requests `bugid` or `both`.
- Local files such as catalogs and profiles are under `local/` by default and are ignored by git.

## CLI Fallback

Use these commands when MCP is not configured:

```powershell
python .\testlink_agent.py list-projects
python .\testlink_agent.py list-plans --project "Project"
python .\testlink_agent.py upload-report --project "Project" --plan "Plan" --platform "Platform" --report "reports\report.txt"
```

Add `--write` only after the preview has been reviewed and confirmed.
