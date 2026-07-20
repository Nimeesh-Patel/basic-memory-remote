# funnel-watchdog.ps1 - self-heal for the 2026-07-19 failure mode (RUNBOOK.md "Troubleshooting"):
# Tailscale's control plane silently drops the Funnel registration and the public DNS
# record reverts to the unroutable tailnet 100.x IP. Detects it by asking Tailscale's
# authoritative nameservers directly (same answer the world sees) and re-registers.
# Runs from Task Scheduler task "basic-memory-funnel-watchdog" (every 15 min + on wake/logon).

$hostname    = 'lenovoideapad.tailec13e9.ts.net'
$nameservers = @('ns1.dnsimple.com', 'ns2.dnsimple-edge.net', 'ns3.dnsimple.com')
$logFile     = Join-Path $PSScriptRoot 'watchdog.log'
$stateFile   = Join-Path $PSScriptRoot 'watchdog.state'
$cooldownMin = 30   # don't re-remediate while DNS (TTL 600s) is still propagating

function Write-Log([string]$msg) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $msg
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

# keep the log from growing unbounded
try {
    if ((Test-Path $logFile) -and (Get-Item $logFile).Length -gt 500KB) {
        $tail = Get-Content $logFile -Tail 200
        Set-Content $logFile $tail -Encoding utf8
    }
} catch {}

# 1. Only act if Funnel is meant to be on (never fight an intentional funnel-off).
$serveJson = cmd /c "tailscale serve status --json 2>nul"
if ($LASTEXITCODE -ne 0 -or -not $serveJson) { Write-Log 'SKIP: tailscale CLI not reachable'; exit 0 }
try { $serve = ($serveJson -join "`n") | ConvertFrom-Json } catch { Write-Log 'SKIP: could not parse serve status'; exit 0 }
$funnelWanted = $false
if ($serve.AllowFunnel) {
    foreach ($p in $serve.AllowFunnel.PSObject.Properties) { if ($p.Value) { $funnelWanted = $true } }
}
if (-not $funnelWanted) { Write-Log 'SKIP: funnel not enabled locally'; exit 0 }

# 2. Ask the authoritative nameservers what the public internet sees.
$answers = $null
foreach ($ns in $nameservers) {
    try {
        $answers = Resolve-DnsName $hostname -Type A -Server $ns -DnsOnly -ErrorAction Stop |
                   Where-Object { $_.Type -eq 'A' }
        if ($answers) { break }
    } catch { continue }
}
if (-not $answers) { Write-Log 'SKIP: no authoritative DNS answer (offline?)'; exit 0 }

$ips = @($answers | ForEach-Object { $_.IPAddress })
# Healthy = at least one public ingress IP; broken = only CGNAT 100.64.0.0/10 (tailnet IP)
$publicIps = @($ips | Where-Object {
    $o = $_.Split('.')
    -not ($o[0] -eq '100' -and [int]$o[1] -ge 64 -and [int]$o[1] -le 127)
})
if ($publicIps.Count -gt 0) { Write-Log "OK: $($ips -join ', ')"; exit 0 }

# 3. Broken. Respect the cooldown so we don't reset-loop during propagation.
if (Test-Path $stateFile) {
    $lastTime = [datetime]::MinValue
    $last = (Get-Content $stateFile | Select-Object -First 1)
    if ([datetime]::TryParse($last, [ref]$lastTime) -and ((Get-Date) - $lastTime).TotalMinutes -lt $cooldownMin) {
        Write-Log "BROKEN ($($ips -join ', ')) but remediated at $last - waiting out cooldown"
        exit 0
    }
}

Write-Log "BROKEN: public DNS reverted to $($ips -join ', ') - running serve reset + funnel re-apply"
Set-Content $stateFile ('{0:yyyy-MM-dd HH:mm:ss}' -f (Get-Date)) -Encoding utf8
cmd /c "tailscale serve reset 2>nul" | Out-Null
Start-Sleep -Seconds 2
$out = cmd /c "tailscale funnel --bg --https=443 http://127.0.0.1:8080 2>&1"
if ($LASTEXITCODE -eq 0) {
    Write-Log 'REMEDIATED: funnel re-applied; DNS should flip within ~3 min (TTL 600s)'
} else {
    Write-Log "ERROR: funnel re-apply failed: $($out -join ' | ')"
}
