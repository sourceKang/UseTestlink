# GitHub Release Deployment

## Purpose

`D:\UseTestlink` is the development checkout. Codex projects that consume the MCPs
must use a user-level isolated installation downloaded from a reviewed GitHub release
tag. They must not depend on this checkout through `cwd`, editable installation, or
source paths.

Credential files are machine-local configuration. They are never downloaded from
GitHub, committed to the repository, or embedded in a package.

## Release Preparation

1. Update the version in `pyproject.toml`.
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
pipx install "git+https://github.com/sourceKang/UseTestlink.git@v1.6.0"
```

If an editable or obsolete installation already exists, replace it explicitly:

```powershell
pipx uninstall testlink-agent
pipx install "git+https://github.com/sourceKang/UseTestlink.git@v1.6.0"
```

Run `pipx ensurepath` once if the pipx binary directory is not already on `PATH`, then
open a new terminal.

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

After a newer reviewed tag is published:

```powershell
pipx install --force "git+https://github.com/sourceKang/UseTestlink.git@v1.6.0"
```

Restart Codex and repeat source, entrypoint, and read-only smoke verification. Avoid
installing directly from a moving `main` branch for formal use because it is not a
reproducible release identity.

## Rollback

Reinstall the previously approved tag:

```powershell
pipx install --force "git+https://github.com/sourceKang/UseTestlink.git@v1.5.0"
```

Restart Codex and verify the installed version. Rollback changes only the local MCP
package. It does not delete or overwrite TestLink executions, Redmine issues, or audit
records.
