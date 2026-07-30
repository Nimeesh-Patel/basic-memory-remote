#Requires -RunAsAdministrator

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$stateDirectory = [System.IO.Path]::GetFullPath('C:\ProgramData\Tailscale')
$statePath = [System.IO.Path]::GetFullPath(
    (Join-Path $stateDirectory 'server-state.conf')
)

if (-not $statePath.StartsWith(
    $stateDirectory + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to move state outside $stateDirectory"
}
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    throw "Tailscale state file not found: $statePath"
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupPath = [System.IO.Path]::GetFullPath(
    (Join-Path $stateDirectory "server-state.before-identity-reset-$timestamp.conf")
)
if (Test-Path -LiteralPath $backupPath) {
    throw "Backup path already exists: $backupPath"
}

$service = Get-Service -Name 'Tailscale'
$service.Stop()
$service.WaitForStatus(
    [System.ServiceProcess.ServiceControllerStatus]::Stopped,
    [TimeSpan]::FromSeconds(45)
)

try {
    Move-Item -LiteralPath $statePath -Destination $backupPath
    $service.Start()
    $service.WaitForStatus(
        [System.ServiceProcess.ServiceControllerStatus]::Running,
        [TimeSpan]::FromSeconds(45)
    )
} catch {
    if (-not (Test-Path -LiteralPath $statePath) -and
        (Test-Path -LiteralPath $backupPath)) {
        Move-Item -LiteralPath $backupPath -Destination $statePath
    }
    if ((Get-Service -Name 'Tailscale').Status -ne 'Running') {
        Start-Service -Name 'Tailscale'
    }
    throw
}

[pscustomobject]@{
    service        = 'Tailscale'
    status         = (Get-Service -Name 'Tailscale').Status.ToString()
    state_backup   = $backupPath
    reset_complete = -not (Test-Path -LiteralPath $statePath)
} | ConvertTo-Json -Compress
