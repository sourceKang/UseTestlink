# Redmine Sandbox

This directory is for a development-only Redmine sandbox.

Do not use this sandbox as the corporate defect system. Formal bugs, RM#, eITS#, release notes, and PQA import flows must use the corporate Redmine/eITS environment.

## Purpose

Use this sandbox only to test `testlink-agent` behavior such as:

- Redmine API connectivity
- Issue payload templates
- Dedupe marker lookup
- Evidence comment writes
- Manager-only field blocking
- Retry and audit-log behavior

## Start

Copy the example environment file:

```powershell
Copy-Item .\infra\redmine-sandbox\.env.example .\infra\redmine-sandbox\.env
```

Start the sandbox:

```powershell
docker compose --env-file .\infra\redmine-sandbox\.env -f .\infra\redmine-sandbox\docker-compose.yml up -d
```

Open:

```text
http://localhost:3001
```

## Stop

```powershell
docker compose --env-file .\infra\redmine-sandbox\.env -f .\infra\redmine-sandbox\docker-compose.yml down
```

To delete sandbox data, remove the Docker volumes manually after confirming they are not needed.

## Agent Profile

A sandbox agent env file should use:

```text
TESTLINK_AGENT_PROFILE=sandbox
REDMINE_ENV=sandbox
REDMINE_URL=http://localhost:3001
```

Never copy sandbox issue IDs or release-note output into formal TestLink/PQA flows.

## Production Warning

This compose file is not a production deployment recipe. It does not define corporate authentication, backup, monitoring, TLS, mail delivery, or upgrade policy.
