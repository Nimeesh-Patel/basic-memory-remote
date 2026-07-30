# funnel-watchdog.ps1 - self-heal for the 2026-07-19 failure mode (RUNBOOK.md "Troubleshooting"):
# Tailscale's control plane silently drops the Funnel registration and the public DNS
# record reverts to the unroutable tailnet 100.x IP. Detects it through an external
# DNS-over-HTTPS resolver so Windows/Tailscale MagicDNS cannot substitute a private answer.
# Runs from Task Scheduler task "basic-memory-funnel-watchdog" (every 15 min + on wake/logon).

[CmdletBinding()]
param(
    # Inspect local configuration and external public DNS without changing state.
    [switch]$StatusOnly
)

$logFile     = Join-Path $PSScriptRoot 'watchdog.log'
$stateFile   = Join-Path $PSScriptRoot 'watchdog.state'
$repairFile  = Join-Path $PSScriptRoot 'watchdog.repair'
$verifyMin   = 12   # public DNS TTL is 600s; judge only after propagation

function Write-Log([string]$msg) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $msg
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

# Command acceptance is not recovery. Later runs verify external public DNS.
function Invoke-FunnelApply([string]$context) {
    $out = cmd /c "tailscale funnel --bg --https=443 http://127.0.0.1:8080 2>&1"
    if ($LASTEXITCODE -eq 0) {
        Remove-Item $repairFile -ErrorAction SilentlyContinue
        if (-not (Test-Path $stateFile)) {
            Set-Content $stateFile ('{0:yyyy-MM-dd HH:mm:ss}' -f (Get-Date)) -Encoding utf8
        }
        Write-Log "${context}: funnel re-applied locally; awaiting external DNS verification"
        return $true
    } else {
        Write-Log "ERROR: funnel re-apply failed (${context}): $($out -join ' | ')"
        return $false
    }
}

# keep the log from growing unbounded
if (-not $StatusOnly) {
    try {
        if ((Test-Path $logFile) -and (Get-Item $logFile).Length -gt 500KB) {
            $tail = Get-Content $logFile -Tail 200
            Set-Content $logFile $tail -Encoding utf8
        }
    } catch {}
}

# 1. Only act if Funnel is meant to be on (never fight an intentional funnel-off).
$serveJson = cmd /c "tailscale serve status --json 2>nul"
if ($LASTEXITCODE -ne 0 -or -not $serveJson) {
    if ($StatusOnly) { [pscustomobject]@{ status = 'unknown'; reason = 'tailscale CLI not reachable' } | ConvertTo-Json -Compress }
    else { Write-Log 'SKIP: tailscale CLI not reachable' }
    exit 0
}
try { $serve = ($serveJson -join "`n") | ConvertFrom-Json } catch {
    if ($StatusOnly) { [pscustomobject]@{ status = 'unknown'; reason = 'could not parse serve status' } | ConvertTo-Json -Compress }
    else { Write-Log 'SKIP: could not parse serve status' }
    exit 0
}
$funnelWanted = $false
$funnelHost = $null
if ($serve.AllowFunnel) {
    foreach ($p in $serve.AllowFunnel.PSObject.Properties) {
        if ($p.Value) {
            $funnelWanted = $true
            if (-not $funnelHost) { $funnelHost = $p.Name -replace ':\d+$', '' }
        }
    }
}
if (-not $funnelWanted) {
    # `serve reset` below clears AllowFunnel, so a remediation interrupted between
    # the reset and the re-apply looks identical to an intentional funnel-off, and
    # this guard would skip forever (it did, from 2026-07-25 23:47 onward). The
    # marker separates the two: only ever finish a repair this script started.
    if (Test-Path $repairFile) {
        if ($StatusOnly) {
            [pscustomobject]@{ status = 'repair-interrupted'; funnel_enabled = $false } | ConvertTo-Json -Compress
        } else {
            Write-Log 'RESUMING: funnel off with a repair marker present - finishing interrupted repair'
            $applied = Invoke-FunnelApply 'ATTEMPTED (resumed repair)'
            if (-not $applied) { exit 2 }
        }
    } else {
        if ($StatusOnly) {
            [pscustomobject]@{ status = 'disabled'; funnel_enabled = $false } | ConvertTo-Json -Compress
        } else {
            Remove-Item $stateFile -ErrorAction SilentlyContinue
            Write-Log 'SKIP: funnel not enabled locally'
        }
    }
    exit 0
}
# Funnel is on, so any surviving repair marker is stale.
if (-not $StatusOnly) { Remove-Item $repairFile -ErrorAction SilentlyContinue }
if (-not $funnelHost) {
    if ($StatusOnly) { [pscustomobject]@{ status = 'unknown'; funnel_enabled = $true; reason = 'could not derive Funnel hostname' } | ConvertTo-Json -Compress }
    else { Write-Log 'SKIP: could not derive Funnel hostname from serve status' }
    exit 0
}

# 2. Ask an external DNS-over-HTTPS resolver what the public internet sees.
# Resolve-DnsName is deliberately not used: Tailscale's Windows DNS layer can return
# the private MagicDNS 100.x answer even when -Server names a public/authoritative DNS
# server. Node's fetch bypasses that DNS interception and is already present on this host.
$dohUrl = 'https://dns.google/resolve?name={0}&type=A' -f [uri]::EscapeDataString($funnelHost)
$dohScript = "fetch(process.argv[1],{headers:{accept:'application/dns-json'}}).then(r=>{if(!r.ok)throw Error(String(r.status));return r.text()}).then(console.log).catch(()=>process.exit(1))"
$dohJson = & node -e $dohScript $dohUrl 2>$null
if ($LASTEXITCODE -ne 0 -or -not $dohJson) {
    if ($StatusOnly) {
        [pscustomobject]@{ status = 'unknown'; funnel_enabled = $true; reason = 'external DNS-over-HTTPS lookup failed' } | ConvertTo-Json -Compress
    } else {
        Write-Log 'SKIP: external DNS-over-HTTPS lookup failed (offline or node unavailable?)'
    }
    exit 0
}
try { $doh = ($dohJson -join "`n") | ConvertFrom-Json } catch {
    if ($StatusOnly) {
        [pscustomobject]@{ status = 'unknown'; funnel_enabled = $true; reason = 'could not parse external DNS-over-HTTPS answer' } | ConvertTo-Json -Compress
    } else {
        Write-Log 'SKIP: could not parse external DNS-over-HTTPS answer'
    }
    exit 0
}
$ips = @($doh.Answer | Where-Object { $_.type -eq 1 } | ForEach-Object { $_.data })
if ($ips.Count -eq 0) {
    if ($StatusOnly) {
        [pscustomobject]@{ status = 'broken'; funnel_enabled = $true; hostname = $funnelHost; public_dns_ips = @(); pending_attempt = (Test-Path $stateFile) } | ConvertTo-Json -Compress
    } else {
        Write-Log 'BROKEN: external DNS-over-HTTPS returned no A records'
    }
    exit 2
}
# Healthy = at least one public ingress IP; broken = only CGNAT 100.64.0.0/10 (tailnet IP)
$publicIps = @($ips | Where-Object {
    $o = $_.Split('.')
    -not ($o[0] -eq '100' -and [int]$o[1] -ge 64 -and [int]$o[1] -le 127)
})
if ($StatusOnly) {
    $status = if ($publicIps.Count -gt 0) { 'healthy' } else { 'broken' }
    [pscustomobject]@{
        status = $status
        funnel_enabled = $true
        hostname = $funnelHost
        public_dns_ips = $ips
        pending_attempt = (Test-Path $stateFile)
    } | ConvertTo-Json -Compress
    exit 0
}

if ($publicIps.Count -gt 0) {
    $recovered = Test-Path $stateFile
    Remove-Item $stateFile -ErrorAction SilentlyContinue
    if ($recovered) { Write-Log "RECOVERED: external DNS is public again ($($ips -join ', '))" }
    else { Write-Log "OK: $($ips -join ', ')" }
    exit 0
}

# 3. One repair attempt belongs to one incident. Verify its predicted effect
# after DNS propagation; never mistake command acceptance for recovery.
if (Test-Path $stateFile) {
    $lastTime = [datetime]::MinValue
    $last = (Get-Content $stateFile | Select-Object -First 1)
    if ([datetime]::TryParse($last, [ref]$lastTime)) {
        $ageMin = ((Get-Date) - $lastTime).TotalMinutes
        if ($ageMin -lt $verifyMin) {
            Write-Log "PENDING: DNS still $($ips -join ', ') after attempt at $last; waiting for the ${verifyMin}-minute verification window"
            exit 0
        }
    }
    Write-Log "UNHEALED: DNS is still $($ips -join ', ') after the repair attempted at $last; stopping automatic resets because the failure is outside the local Funnel configuration"
    exit 2
}

Write-Log "BROKEN: public DNS reverted to $($ips -join ', ') - attempting one serve reset + funnel re-apply"
Set-Content $stateFile ('{0:yyyy-MM-dd HH:mm:ss}' -f (Get-Date)) -Encoding utf8
# Marker first: from here until the re-apply succeeds, a funnel-off state is ours.
Set-Content $repairFile ('{0:yyyy-MM-dd HH:mm:ss}' -f (Get-Date)) -Encoding utf8
cmd /c "tailscale serve reset 2>nul" | Out-Null
Start-Sleep -Seconds 2
$applied = Invoke-FunnelApply 'ATTEMPTED'
if (-not $applied) { exit 2 }
