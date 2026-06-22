param(
    [string]$OutputDirectory = "github_upload"
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Resolve-Path (Join-Path $scriptDir "..")
$output = Join-Path $root $OutputDirectory
$outputFullPath = [System.IO.Path]::GetFullPath($output)
$rootFullPath = [System.IO.Path]::GetFullPath($root)

if (-not $outputFullPath.StartsWith($rootFullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Output directory must stay inside the workspace: $outputFullPath"
}

if ([System.IO.Path]::GetFileName($outputFullPath) -ne $OutputDirectory) {
    throw "Unexpected output directory path: $outputFullPath"
}

if (Test-Path -LiteralPath $outputFullPath) {
    Remove-Item -LiteralPath $outputFullPath -Recurse -Force
}

New-Item -ItemType Directory -Path $outputFullPath | Out-Null
New-Item -ItemType Directory -Path (Join-Path $outputFullPath "tests") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $outputFullPath "testlink_agent_core") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $outputFullPath "skills") | Out-Null

$copies = @(
    @{ Source = "testlink_agent.py"; Destination = "testlink_agent.py" },
    @{ Source = "pyproject.toml"; Destination = "pyproject.toml" },
    @{ Source = "AGENT.md"; Destination = "AGENT.md" },
    @{ Source = ".env.example"; Destination = ".env.example" },
    @{ Source = "README.md"; Destination = "README.md" },
    @{ Source = ".gitignore"; Destination = ".gitignore" }
)

foreach ($copy in $copies) {
    $source = Join-Path $root $copy["Source"]
    $destination = Join-Path $outputFullPath $copy["Destination"]
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Required source file is missing: $source"
    }
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

function Copy-FilteredDirectory {
    param(
        [string]$SourceDirectory,
        [string]$DestinationDirectory
    )

    $sourceFullPath = Join-Path $root $SourceDirectory
    $destinationFullPath = Join-Path $outputFullPath $DestinationDirectory
    if (-not (Test-Path -LiteralPath $sourceFullPath)) {
        throw "Required source directory is missing: $sourceFullPath"
    }

    Get-ChildItem -Path $sourceFullPath -Recurse -File |
        Where-Object {
            $_.FullName -notmatch "\\__pycache__\\" -and
            $_.Name -notlike "*.pyc" -and
            $_.Name -notlike "*_testcases.json" -and
            $_.Name -notlike "*_testcases.xlsx" -and
            $_.Name -notin @("testcases.json", "testcases.xlsx")
        } |
        ForEach-Object {
            $relative = $_.FullName.Substring($sourceFullPath.Length + 1)
            $destination = Join-Path $destinationFullPath $relative
            $destinationParent = Split-Path -Parent $destination
            if (-not (Test-Path -LiteralPath $destinationParent)) {
                New-Item -ItemType Directory -Path $destinationParent | Out-Null
            }
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
}

Copy-FilteredDirectory -SourceDirectory "testlink_agent_core" -DestinationDirectory "testlink_agent_core"
Copy-FilteredDirectory -SourceDirectory "tests" -DestinationDirectory "tests"
Copy-FilteredDirectory -SourceDirectory "skills" -DestinationDirectory "skills"

$forbiddenRelativePaths = @(
    ".env",
    ".git",
    "downloads",
    "reports",
    "local",
    "output",
    "outputs",
    "__pycache__",
    "tests\__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache"
)

foreach ($relativePath in $forbiddenRelativePaths) {
    $candidate = Join-Path $outputFullPath $relativePath
    if (Test-Path -LiteralPath $candidate) {
        throw "Forbidden local-only path copied to GitHub upload package: $relativePath"
    }
}

$forbiddenFilePatterns = @(
    "*_testcases.json",
    "*_testcases.xlsx",
    "testcases.json",
    "testcases.xlsx",
    "*.pyc"
)

foreach ($pattern in $forbiddenFilePatterns) {
    $matches = Get-ChildItem -Path $outputFullPath -Recurse -File -Filter $pattern
    if ($matches) {
        $names = ($matches | ForEach-Object { $_.FullName.Substring($outputFullPath.Length + 1) }) -join ", "
        throw "Forbidden generated/local files copied to GitHub upload package: $names"
    }
}

$secretPatterns = @(
    "TESTLINK_DEVKEY\s*=\s*(?![""']?(?:replace-with|your-|$)).+",
    "REDMINE_API_KEY\s*=\s*(?![""']?(?:replace-with|your-|$)).+",
    "X-Redmine-API-Key\s*:\s*(?!your-|replace-with).+",
    "\b10\.(?:\d{1,3}\.){2}\d{1,3}\b",
    "\b172\.(?:1[6-9]|2\d|3[0-1])\.(?:\d{1,3}\.)\d{1,3}\b",
    "\b192\.168\.\d{1,3}\.\d{1,3}\b",
    "\bZT\d+\b"
)

$textFiles = Get-ChildItem -Path $outputFullPath -Recurse -File |
    Where-Object { $_.Extension -in @(".py", ".md", ".example", ".gitignore", "") -or $_.Name -eq ".env.example" }

foreach ($file in $textFiles) {
    $content = Get-Content -LiteralPath $file.FullName -Raw
    foreach ($pattern in $secretPatterns) {
        if ($content -match $pattern) {
            $relative = $file.FullName.Substring($outputFullPath.Length + 1)
            throw "Possible real credential found in GitHub upload package: $relative"
        }
    }
}

Write-Host "GitHub upload package rebuilt: $outputFullPath"
Write-Host "Files:"
Get-ChildItem -Path $outputFullPath -Recurse -File |
    Sort-Object FullName |
    ForEach-Object { Write-Host ("- " + $_.FullName.Substring($outputFullPath.Length + 1)) }
