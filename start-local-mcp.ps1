param(
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$UserProfile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
$BasicMemory = if ([string]::IsNullOrWhiteSpace($env:BASIC_MEMORY_EXECUTABLE)) {
    Join-Path $UserProfile ".local\bin\basic-memory.exe"
} else {
    $env:BASIC_MEMORY_EXECUTABLE
}
$Project = if ([string]::IsNullOrWhiteSpace($env:BASIC_MEMORY_PROJECT)) {
    "memory"
} else {
    $env:BASIC_MEMORY_PROJECT
}
$MemoryDirectoryName = "memory"

function Write-Diagnostic {
    param([string]$Message)
    [Console]::Error.WriteLine("[basic-memory-preflight] $Message")
}

function Resolve-ExistingDirectory {
    param([string]$Path)

    try {
        $resolved = Resolve-Path -LiteralPath $Path -ErrorAction Stop
        if (-not (Test-Path -LiteralPath $resolved.ProviderPath -PathType Container)) {
            throw "not a directory"
        }
        return [IO.Path]::GetFullPath($resolved.ProviderPath)
    } catch {
        Write-Diagnostic "Memory project path is not an existing directory: $Path"
        exit 1
    }
}

function Read-ProjectInfo {
    $output = & $BasicMemory project info $Project --json --local 2>&1
    $exitCode = $LASTEXITCODE
    $text = $output | Out-String
    if ($exitCode -ne 0) {
        Write-Diagnostic $text.Trim()
        exit $exitCode
    }

    try {
        return $text | ConvertFrom-Json
    } catch {
        Write-Diagnostic "Could not parse project health JSON: $($_.Exception.Message)"
        exit 1
    }
}

function Assert-ProjectScope {
    param($ProjectInfo)

    if (-not [StringComparer]::Ordinal.Equals([string]$ProjectInfo.project_name, $Project)) {
        Write-Diagnostic "Requested project '$Project' resolved as '$($ProjectInfo.project_name)'."
        exit 1
    }

    $configuredPath = Resolve-ExistingDirectory ([string]$ProjectInfo.project_path)
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals(
        [IO.Path]::GetFileName($configuredPath),
        $MemoryDirectoryName
    )) {
        Write-Diagnostic "Project '$Project' must point to the '$MemoryDirectoryName' subtree, not its vault root: $configuredPath"
        exit 1
    }

    $expectedPath = if ([string]::IsNullOrWhiteSpace($env:BASIC_MEMORY_PROJECT_PATH)) {
        Resolve-ExistingDirectory (Join-Path (Split-Path -Parent $configuredPath) $MemoryDirectoryName)
    } else {
        Resolve-ExistingDirectory $env:BASIC_MEMORY_PROJECT_PATH
    }

    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($configuredPath, $expectedPath)) {
        Write-Diagnostic "Project '$Project' is scoped to '$configuredPath', expected '$expectedPath'."
        exit 1
    }

    # The remote launcher inherits these resolved values instead of owning a
    # second set of executable, project, and path defaults.
    $env:BASIC_MEMORY_EXECUTABLE = $BasicMemory
    $env:BASIC_MEMORY_PROJECT = $Project
    $env:BASIC_MEMORY_PROJECT_PATH = $configuredPath
}

if (-not (Test-Path -LiteralPath $BasicMemory -PathType Leaf)) {
    Write-Diagnostic "Executable not found: $BasicMemory"
    exit 1
}
$BasicMemory = (Resolve-Path -LiteralPath $BasicMemory).ProviderPath

Write-Diagnostic "Validating the configured scope for project '$Project'."
$projectInfo = Read-ProjectInfo
Assert-ProjectScope $projectInfo

Write-Diagnostic "Checking and incrementally repairing embeddings for project '$Project'."
$reindexOutput = & $BasicMemory reindex --project $Project --embeddings 2>&1
if ($LASTEXITCODE -ne 0) {
    $reindexOutput | ForEach-Object { Write-Diagnostic $_ }
    exit $LASTEXITCODE
}

$projectInfo = Read-ProjectInfo
Assert-ProjectScope $projectInfo

$embedding = $projectInfo.embedding_status
$healthy = (
    $embedding.semantic_search_enabled -eq $true -and
    $embedding.reindex_recommended -eq $false -and
    [int]$embedding.orphaned_chunks -eq 0 -and
    [int]$embedding.total_entities_with_chunks -eq [int]$embedding.total_indexed_entities
)

if (-not $healthy) {
    Write-Diagnostic ("Embedding health check failed: indexed={0}, embedded={1}, orphaned={2}, reindex_recommended={3}" -f `
        $embedding.total_indexed_entities,
        $embedding.total_entities_with_chunks,
        $embedding.orphaned_chunks,
        $embedding.reindex_recommended)
    exit 1
}

Write-Diagnostic ("Healthy: {0}/{0} entities embedded, {1} embeddings, zero orphaned." -f `
    $embedding.total_indexed_entities,
    $embedding.total_embeddings)

if ($CheckOnly) {
    exit 0
}

# stdout must remain exclusively available to MCP JSON-RPC.
& $BasicMemory mcp --project $Project
exit $LASTEXITCODE
