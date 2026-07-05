<#
  Starts both services for the remote basic-memory endpoint:
    - basic-memory backend on 127.0.0.1:8000 (loopback only)
    - FastMCP OAuth proxy   on 127.0.0.1:8080 (reads .env)
  Requires .env to be filled in first (see RUNBOOK.md).
#>
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path (Join-Path $here ".env"))) {
  Write-Error "No .env found. Copy .env.example to .env and fill it in (see RUNBOOK.md)."
  exit 1
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$bm = "C:\Users\nimee\.local\bin\basic-memory.exe"
$py = Join-Path $here ".venv\Scripts\python.exe"

Write-Host "Starting basic-memory backend on 127.0.0.1:8000 ..."
Start-Process -FilePath $bm `
  -ArgumentList "mcp","--transport","streamable-http","--host","127.0.0.1","--port","8000" `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $here "bm.out.log") `
  -RedirectStandardError  (Join-Path $here "bm.err.log")

Write-Host "Starting OAuth proxy on 127.0.0.1:8080 ..."
Start-Process -FilePath $py -ArgumentList (Join-Path $here "proxy.py") `
  -WorkingDirectory $here `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $here "proxy.out.log") `
  -RedirectStandardError  (Join-Path $here "proxy.err.log")

Write-Host "Both started. Logs: bm.*.log, proxy.*.log in $here"
Write-Host "Stop with:  Get-Process basic-memory,python | Stop-Process  (or close them individually)"
