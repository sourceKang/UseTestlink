[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TestLinkSource = "local/testlink_agent.env",
    [string]$RedmineSource = ".env",
    [string]$TestLinkOutput = "local/testlink_mcp.env",
    [string]$RedmineOutput = "local/redmine_mcp.env",
    [ValidateSet("corp", "sandbox")]
    [string]$Environment = "corp",
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Read-EnvMap {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Env source does not exist: $Path"
    }
    $values = [ordered]@{}
    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding utf8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }
        $key, $value = $line.Split("=", 2)
        $key = $key.Trim()
        $value = $value.Trim().Trim('"').Trim("'")
        if ($key -match '^[A-Za-z_][A-Za-z0-9_]*$') {
            $values[$key] = $value
        }
    }
    return $values
}

function Require-EnvValue {
    param(
        [Parameter(Mandatory = $true)]$Values,
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Source
    )
    if (-not $Values.Contains($Key) -or [string]::IsNullOrWhiteSpace([string]$Values[$Key])) {
        throw "Required key $Key is missing from $Source"
    }
}

function Write-EnvFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Values,
        [Parameter(Mandatory = $true)][string[]]$Keys,
        [Parameter(Mandatory = $true)]$Overrides
    )

    if ((Test-Path -LiteralPath $Path) -and -not $Force) {
        throw "Output already exists: $Path. Use -Force only after reviewing the target."
    }
    $directory = Split-Path -Parent $Path
    if ($directory) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($key in $Keys) {
        $value = if ($Overrides.Contains($key)) { $Overrides[$key] } elseif ($Values.Contains($key)) { $Values[$key] } else { $null }
        if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) {
            continue
        }
        $text = [string]$value
        if ($text.Contains("`r") -or $text.Contains("`n")) {
            throw "Multiline env value is not supported: $key"
        }
        $lines.Add("$key=$text")
    }
    if ($PSCmdlet.ShouldProcess($Path, "Write allowlisted MCP environment file")) {
        [System.IO.File]::WriteAllLines(
            [System.IO.Path]::GetFullPath($Path),
            [string[]]$lines,
            [System.Text.UTF8Encoding]::new($false)
        )
    }
}

$testlink = Read-EnvMap -Path $TestLinkSource
$redmine = Read-EnvMap -Path $RedmineSource
Require-EnvValue -Values $testlink -Key "TESTLINK_URL" -Source $TestLinkSource
Require-EnvValue -Values $testlink -Key "TESTLINK_DEVKEY" -Source $TestLinkSource
Require-EnvValue -Values $redmine -Key "REDMINE_URL" -Source $RedmineSource
Require-EnvValue -Values $redmine -Key "REDMINE_API_KEY" -Source $RedmineSource

$testlinkKeys = @(
    "TESTLINK_AGENT_PROFILE",
    "TESTLINK_URL",
    "TESTLINK_DEVKEY",
    "TESTLINK_AUTHOR_LOGIN"
)
$redmineKeys = @(
    "REDMINE_ENV",
    "REDMINE_URL",
    "REDMINE_API_KEY",
    "REDMINE_PROJECT_ID",
    "REDMINE_TEMPLATE",
    "REDMINE_TRACKER_ID",
    "REDMINE_PRIORITY_ID",
    "REDMINE_CATEGORY_ID"
)

Write-EnvFile -Path $TestLinkOutput -Values $testlink -Keys $testlinkKeys -Overrides @{ TESTLINK_AGENT_PROFILE = $Environment }
Write-EnvFile -Path $RedmineOutput -Values $redmine -Keys $redmineKeys -Overrides @{ REDMINE_ENV = $Environment }

[pscustomobject]@{
    environment = $Environment
    testlink_output = $TestLinkOutput
    redmine_output = $RedmineOutput
    testlink_keys = @($testlinkKeys | Where-Object { $_ -eq "TESTLINK_AGENT_PROFILE" -or $testlink.Contains($_) })
    redmine_keys = @($redmineKeys | Where-Object { $_ -eq "REDMINE_ENV" -or $redmine.Contains($_) })
    manager_fields_copied = $false
    secret_values_printed = $false
} | ConvertTo-Json -Depth 3
