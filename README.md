# basic-memory-remote

One persistent Markdown memory shared by local Codex/Claude Code and remote ChatGPT/Claude web.

## Architecture

```text
Codex / Claude Code  → local stdio MCP ───────────────┐
                                                       ├→ <vault>\memory
ChatGPT / Claude web → Tailscale Funnel → OAuth proxy → local HTTP MCP
```

Local Codex does not use Tailscale. The remote bridge exists only for web clients that cannot start a local process.

## Invariants

- Project `memory` points only to `<vault>\memory`; never the vault root. The
  startup preflight validates that scope before any reindex or server launch.
- Both MCP servers are explicitly restricted to that project. The proxy inherits
  its policy path from the same validated project path.
- Basic Memory remains loopback-only. Funnel exposes only the OAuth proxy.
- MCP stdout is JSON-RPC only; health diagnostics go to stderr.
- Embedding health is checked at startup, not by a recurring watchdog.

## Files

| File | Role |
|---|---|
| `start-local-mcp.ps1` | Incrementally repairs embeddings, verifies index health, then starts local stdio MCP. `-CheckOnly` skips MCP startup. |
| `start.ps1` | Runs the same check, then starts the HTTP backend and OAuth proxy for web clients. |
| `proxy.py` | GitHub OAuth, fail-closed single-user authorization, and policy-description loading. |
| `.env.example` | Remote configuration template; copy to `.env`. |
| `README.md` | The only operating and rebuild documentation. |

## Local Codex

The Codex configuration under the current user profile points to the wrapper at:

```powershell
$wrapper = Join-Path $env:USERPROFILE "basic-memory-remote\start-local-mcp.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $wrapper
```

The wrapper requires semantic search enabled, every indexed entity embedded, zero orphaned chunks, and no recommended reindex.

```powershell
.\start-local-mcp.ps1 -CheckOnly
codex mcp list
```

Restart Codex after MCP or permission changes. Existing conversations retain their original client and sandbox state.

## Remote web setup

1. Copy `.env.example` to `.env`; set `BASE_URL`, GitHub OAuth client ID/secret, and `ALLOWED_GITHUB_USER`.
2. Create a GitHub OAuth App with homepage `<BASE_URL>` and callback `<BASE_URL>/auth/callback`.
3. Publish and start:

```powershell
tailscale funnel --bg --https=443 http://127.0.0.1:8080
.\start.ps1
```

4. Register `<BASE_URL>/mcp` in ChatGPT or Claude web and complete GitHub OAuth.
5. Verify from outside the tailnet:

```powershell
curl.exe -i <BASE_URL>/mcp
curl.exe <BASE_URL>/.well-known/oauth-protected-resource/mcp
```

The first request should return `401` with `WWW-Authenticate`; metadata should return `200`. In-tailnet curl and a successful `tailscale funnel` command do not prove public reachability. Diagnose Funnel through DNS-over-HTTPS or another external vantage.

## Operations

```powershell
# Index health
.\start-local-mcp.ps1 -CheckOnly

# Remote state
Get-Process basic-memory,python
tailscale funnel status

# Disable public access; local MCP remains available
tailscale funnel --https=443 off
```

Remote logs: `bm.out.log`, `bm.err.log`, `proxy.out.log`, and `proxy.err.log`.

There is deliberately no scheduled watchdog. Repair the specific external DNS/OAuth/process failure when it occurs; local Codex remains independent.

## Rebuild

```powershell
python -m pip install uv
python -m uv tool install basic-memory
$memoryPath = Join-Path "<vault>" "memory"
basic-memory project add memory $memoryPath
basic-memory project default memory
basic-memory project info memory --json --local

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install fastmcp==3.4.2
```

Then complete Remote web setup. The user Startup entry `basic-memory-remote.cmd` may call `start.ps1` at logon; delete it if remote web availability is no longer wanted.

## Policy loading

The proxy reads `policies\Policy Loader.md` and active policy notes beneath
`policies\` within the validated Basic Memory project when composing MCP
instructions/tool descriptions. It does not evaluate or enforce note content.
Local CLIs load policy through their agent/Perspirator runtime.

## Security

- GitHub identity is checked against `ALLOWED_GITHUB_USER` on every remote request/tool call.
- Basic Memory and the proxy bind to loopback; Funnel publishes only the proxy.
- Secrets live only in `.env`; rotate the GitHub OAuth secret if exposed.

## Verification of the bridge

`proxy_config.py` enforces the loopback backend and binding invariant before the
proxy is constructed. Policy-read/enrichment failure carries an explicit
unavailable disclosure; it must not silently return unqualified descriptions.
Run `.\.venv\Scripts\python.exe -m unittest test_proxy -v` for bounded
configuration, policy-failure and authorization checks. The tests use fixture
credentials and do not contact GitHub or start a server.

The launcher reports the exact process IDs it starts. Its output establishes a
launch request, not endpoint readiness. Stop only those owned processes; never
stop every Python or Basic Memory process by name. Local stdio clients remain
independent of the optional remote backend.
