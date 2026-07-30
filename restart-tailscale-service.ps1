#Requires -RunAsAdministrator

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$serviceName = 'Tailscale'
$service = Get-Service -Name $serviceName
$previousProcessId = (Get-CimInstance Win32_Service -Filter "Name='$serviceName'").ProcessId

Restart-Service -Name $serviceName -Force
$service.WaitForStatus(
    [System.ServiceProcess.ServiceControllerStatus]::Running,
    [TimeSpan]::FromSeconds(45)
)

$currentProcessId = (Get-CimInstance Win32_Service -Filter "Name='$serviceName'").ProcessId
if (-not $currentProcessId) {
    throw 'Tailscale returned to Running without a service process ID.'
}

[pscustomobject]@{
    service             = $serviceName
    status              = (Get-Service -Name $serviceName).Status.ToString()
    previous_process_id = $previousProcessId
    current_process_id  = $currentProcessId
    process_replaced    = $previousProcessId -ne $currentProcessId
} | ConvertTo-Json -Compress
