# TestLink Agent CLI

TestLink Agent CLI is a small Python XML-RPC helper for department TestLink workflows.
It is designed for Codex/agent use and for engineers who need a repeatable way to preview
and upload automation results.

## Safety Rules

- Do not commit personal API keys.
- Each user must use their own TestLink `Personal API access key`.
- The default `upload-report` mode is preview only. Add `--write` only after reviewing the preview.
- Result upload appends execution records by default; it does not use overwrite.
- Native TestLink bug-linking is not used. Until the department bug integration is defined, put bug IDs in execution notes only.
- Custom release-note or bug-linking pages may not be available through the native TestLink XML-RPC API used by this CLI. Update this tool only after the TestLink owner provides the supported API, database table, or access method.
- Destructive actions such as deletion or overwrite are intentionally not implemented in this CLI.

## Setup

Requires Python 3.10+ and only the Python standard library.

Create environment variables:

```powershell
$env:TESTLINK_URL="https://your-testlink.example.com/testlink"
$env:TESTLINK_DEVKEY="your-personal-api-access-key"
```

Or copy `.env.example` to `.env` and pass `--env-file .env`.

## List Projects

```powershell
python .\testlink_agent.py list-projects
```

## Preview A Report Upload

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore
```

If `--build` and `--build-id` are omitted, the CLI selects the latest active/open build in the target test plan and shows the selected build in preview. You can still specify a build explicitly when needed:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --build "1.2.3 build 5" `
  --report "C:\path\to\report.txt"
```

Preview validates:

- devKey
- project, test plan, platform, and build
- build is active/open
- report rows can map to test cases in the target plan/platform
- duplicate or missing external IDs

## Upload A Report

Add `--write` after the preview looks correct:

```powershell
python .\testlink_agent.py upload-report `
  --project "YourProject" `
  --plan "Your Test Plan" `
  --platform "Your Platform" `
  --report "C:\path\to\report.txt" `
  --skip-policy ignore `
  --write
```

Status mapping:

| Report result | TestLink status |
|---|---|
| `Pass` | `p` |
| `Fail` | `f` |
| `Error` | `f` |
| `Blocked` | `b` |
| `Skip` with `--skip-policy ignore` | not uploaded |
| `Skip` with `--skip-policy blocked` | `b` |

Execution notes include automation source, report generation time, EMS version, node, test function, original result, duration, and report filename.

## Share With Teammates

Publish this repository to the location your team uses for shared tooling. Teammates can clone it, set their own `TESTLINK_URL` and `TESTLINK_DEVKEY`, and run the same commands from their own agent or terminal.

Before making a fork or copy public, make sure examples, docs, logs, and test fixtures do not contain internal URLs, project names, platform names, report paths, or credentials.
