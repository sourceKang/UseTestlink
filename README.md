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
- The department `Release Note Content` UI is custom and is not currently available through the native TestLink XML-RPC API used by this CLI. Update this tool after the TestLink owner provides the custom API, database table, or supported access method.
- Destructive actions such as deletion or overwrite are intentionally not implemented in this CLI.

## Setup

Requires Python 3.10+ and only the Python standard library.

Create environment variables:

```powershell
$env:TESTLINK_URL="http://testlink.zyxel.com/testlink"
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
  --project "NetAtlasEMS" `
  --plan "NetAtlas EMS" `
  --platform "NetAtlas EMS" `
  --build-id "19641" `
  --report "D:\RestApi auto\reports\03.00.11 (AAVV.221) b5\Web_Ems_Rest_Api_03.00.11 (AAVV.221) b5_NeoX-03_NXC400_report_2026-06-12_13-26-09.txt" `
  --skip-policy ignore
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
  --project "NetAtlasEMS" `
  --plan "NetAtlas EMS" `
  --platform "NetAtlas EMS" `
  --build-id "19641" `
  --report "D:\path\to\report.txt" `
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

Publish this repository as a private GitHub or GitHub Enterprise repository. Teammates can clone it, set their own `TESTLINK_DEVKEY`, and run the same commands from their own agent or terminal.

Do not make this repository public if project names, internal URLs, platform names, or report paths are considered internal information.
