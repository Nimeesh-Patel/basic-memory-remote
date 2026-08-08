param(
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$BasicMemory = "C:\Users\nimee\.local\bin\basic-memory.exe"
$Project = "memory"

function Write-Diagnostic {
    param([string]$Message)
    [Console]::Error.WriteLine("[basic-memory-preflight] $Message")
}

if (-not (Test-Path -LiteralPath $BasicMemory -PathType Leaf)) {
    Write-Diagnostic "Executable not found: $BasicMemory"
    exit 1
}

Write-Diagnostic "Checking and incrementally repairing embeddings for project '$Project'."
$reindexOutput = & $BasicMemory reindex --project $Project --embeddings 2>&1
if ($LASTEXITCODE -ne 0) {
    $reindexOutput | ForEach-Object { Write-Diagnostic $_ }
    exit $LASTEXITCODE
}

$projectInfoText = & $BasicMemory project info $Project --json --local 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Write-Diagnostic $projectInfoText.Trim()
    exit $LASTEXITCODE
}

try {
    $projectInfo = $projectInfoText | ConvertFrom-Json
} catch {
    Write-Diagnostic "Could not parse project health JSON: $($_.Exception.Message)"
    exit 1
}

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
& $BasicMemory mcp
exit $LASTEXITCODE
