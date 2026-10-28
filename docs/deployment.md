# GitHub Release Deployment

## Purpose

`D:\UseTestlink` is the development checkout. Codex projects that consume the MCPs
must use a user-level isolated installation downloaded from a reviewed GitHub release
tag. They must not depend on this checkout through `cwd`, editable installation, or
source paths.

Credential files are machine-local configuration. They are never downloaded from
GitHub, committed to the repository, or embedded in a package.

## Release Preparation

1. Update `PACKAGE_VERSION` in `qa_mcp_contracts/release.py`. `pyproject.toml` reads it, and
   every MCP server reports it as its `serverInfo` version and in its startup health line.
2. Synchronize documentation and entrypoint names.
3. Run the complete offline suite:

   ```powershell
   python -m unittest discover -s tests
   ```

4. Build a wheel and install it into a clean temporary virtual environment.
5. Verify that the installed distribution exposes:

   - `testlink-mcp`
   - `redmine-mcp`
   - `qa-integration-agent-mcp`
   - legacy compatibility entrypoints only where documented

6. Confirm that `.env`, `local/`, reports, API keys, devKeys, and downloaded exports
   are not staged.
7. Merge the reviewed change, create an annotated version tag, and push the tag.

Do not publish a release tag from an unreviewed or partially merged branch.

## Install A Tagged Release

Install once per Windows user, not once per consuming project:

```powershell
pipx install "git+https://github.com/sourceKang/UseTestlink.git@v1.9.7"
```

If an editable or obsolete installation already exists, replace it explicitly:

```powershell
pipx uninstall testlink-agent
pipx install "git+https://github.com/sourceKang/UseTestlink.git@v1.9.7"
```

Run `pipx ensurepath` once if the pipx binary directory is not already on `PATH`, then
open a new terminal.

The package also ships the agent skills from `.agents/skills` (except repository-only
skills such as `retro`) and the docs they cite. Copy them into the Claude Code skills
directory with:

```powershell
testlink-agent install-skills            # add --dry-run to preview, --target to choose a directory
```

Each skill is written to `~/.claude/skills/<name>/` with a `.testlink-agent-skill` marker,
its `references/`, and the cited docs under `docs/`; `docs/<name>.md` references are
rewritten to those copies. A same-named directory without the marker is skipped (exit
code 1) unless `--force` is given. Re-run the command after every upgrade.

## Codex Registration

Register only the task-scoped executable in the user-level Codex `config.toml`. Use
`docs/codex-mcp-config.example.toml` for the recommended QA-import default, or
`docs/codex-mcp-config.direct.example.toml` for one direct server. Do not register all
three by default and do not set a repository `cwd`.

Store credentials under a user-controlled location such as:

```text
C:\Users\<username>\.codex\testlink-agent\testlink_mcp.env
C:\Users\<username>\.codex\testlink-agent\redmine_mcp.env
```

The files must not be committed or included in tool arguments. Restart Codex after
changing registration.

## Claude And Diagnostics

See `claude-setup.md` for separate Claude Code/Desktop templates. Both use the same
isolated installation; never point a consuming project at the development checkout.
The `testlink-agent doctor` command (v1.8.0 and later) performs offline checks without
reading credential contents. It does not prove live connectivity or inspect client settings.

## Verify Installation Source And Entrypoints

Confirm all executables resolve outside `D:\UseTestlink`:

```powershell
Get-Command testlink-mcp, redmine-mcp, qa-integration-agent-mcp |
  Select-Object Name, Source
```

Inspect the pipx environment:

```powershell
pipx list
```

The package source must be the GitHub tag, not `file:///D:/UseTestlink`, and must not
be marked editable. Confirm that the installed metadata version matches the tag.

Use only read-only MCP health/about/discovery tools for live smoke checks. Installation
verification never authorizes a TestLink execution or Redmine write.

## Upgrade

Before upgrading to v1.9.3 or later from v1.9.2 or earlier, add
`TESTLINK_BUG_LINK_ENDPOINT=<bug-link script name>` (a bare `name.php`, from your
TestLink administrator) to the TestLink MCP env file. The endpoint name is no longer
built in, so without it `testlink_link_bug` returns `CONFIG_MISSING`; other tools are
unaffected. Do not commit the value.

From v1.9.7, previews and audits written without `artifact_dir` / `audit_dir` go to
`%LOCALAPPDATA%\testlink-agent\` (or `TESTLINK_AGENT_DATA_DIR`) instead of a relative
`local\` that failed under Claude Desktop. Explicit paths and resumes of older audits are
unchanged.

After a newer reviewed tag is published:

```powershell
pipx install --force "git+https://github.com/sourceKang/UseTestlink.git@v1.9.7"
```

Run `testlink-agent install-skills` to refresh the installed skills, restart Codex and
Claude Code, and repeat source, entrypoint, and read-only smoke verification. Avoid
installing directly from a moving `main` branch for formal use because it is not a
reproducible release identity.

## Rollback

Reinstall the previously approved tag:

```powershell
pipx install --force "git+https://github.com/sourceKang/UseTestlink.git@v1.9.6"
```

Restart Codex and verify the installed version. Rollback changes only the local MCP
package. It does not delete or overwrite TestLink executions, Redmine issues, or audit
records.
