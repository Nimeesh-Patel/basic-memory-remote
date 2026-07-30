# basic-memory remote (ChatGPT + Claude web) — runbook

Exposes the local basic-memory MCP server to ChatGPT (Developer Mode) and Claude web
(custom connectors) over an authenticated OAuth 2.1 endpoint, self-hosted and free.

```
[ChatGPT / Claude web] --HTTPS--> Tailscale Funnel (stable ts.net URL)
    --> FastMCP OAuth proxy  127.0.0.1:8080  (GitHub IdP, only YOU allowed)
    --> basic-memory         127.0.0.1:8000  (loopback ONLY)
```

## LIVE since 2026-07-05
- Funnel URL: `https://lenovoideapad.tailec13e9.ts.net` (persistent; MCP endpoint is `/mcp`)
- GitHub OAuth App created; creds in `.env`. Allowed user: `Nimeesh-Patel`.
- All acceptance tests below passed against the public URL.
- Auto-start at logon: `basic-memory-remote.cmd` in the user Startup folder
  (`shell:startup`) runs `start.ps1`. Delete that file to disable.

## Already done (by setup)
- `.venv` here with `fastmcp==3.4.2`.
- `proxy.py` — the OAuth proxy + fail-closed identity middleware (only `ALLOWED_GITHUB_USER`).
- `.env.example` — copy to `.env` and fill in. `.env`/`.venv` are gitignored.
- Validated locally: unauth → 401 + `WWW-Authenticate`; `/.well-known/oauth-protected-resource/mcp` → 200;
  auth-server metadata served at root; backend stays on loopback.

## Remaining steps (interactive — need your accounts)

### A. Tailscale Funnel → get the stable public URL
1. Install Tailscale for Windows: https://tailscale.com/download/windows
2. `tailscale up` (opens browser; log in — Google is fine).
3. Enable Funnel for the tailnet if prompted (Tailscale admin console → Access controls / Funnel).
4. `tailscale funnel 8080` — this prints your stable URL:
   `https://<machine>.<tailnet>.ts.net`. Record it. It survives reboots.
   (Funnel forwards 443 → your local 8080, i.e. the proxy.)

### B. GitHub OAuth App (free) → client id + secret
1. https://github.com/settings/developers → **New OAuth App**.
2. Homepage URL: your Funnel URL. **Authorization callback URL:**
   `https://<machine>.<tailnet>.ts.net/auth/callback`  (path is `/auth/callback`).
3. Create → copy the **Client ID**, generate a **Client secret**.

### C. Fill `.env`
```
cp .env.example .env    # then edit .env
```
Set `BASE_URL` = Funnel URL (no trailing slash), `GH_CLIENT_ID`, `GH_CLIENT_SECRET`,
`ALLOWED_GITHUB_USER` = your exact GitHub login. Secrets live only in `.env`.

### D. Run the two services (keep them running)
From this folder:
```
.\start.ps1
```
It starts basic-memory on 127.0.0.1:8000 and the proxy on 127.0.0.1:8080.
For always-on: add `start.ps1` to Task Scheduler "At log on" (or wrap with NSSM as a
service). The Tailscale Funnel is already persistent once set in step A.

### E. Register the connector
- **ChatGPT** (Plus/Pro): Settings → Connectors → Developer Mode → add custom MCP
  connector with URL `https://<machine>.<tailnet>.ts.net/mcp`. Complete the GitHub
  consent flow. Enable it in the conversation composer.
- **Claude web** (Pro): Settings → Connectors → add custom connector, same `/mcp` URL,
  complete GitHub consent.

## Acceptance tests
NOTE: run these from OUTSIDE the tailnet, or at least verify public DNS first (see
Troubleshooting) — from this laptop the hostname resolves to the tailnet IP and curls
bypass the public Funnel ingress entirely, so they can pass while the public path is dead.
```
# 1. unauth -> 401 with WWW-Authenticate: Bearer resource_metadata="..."
curl -i https://<FUNNEL>/mcp
# 2. discovery -> 200 JSON
curl https://<FUNNEL>/.well-known/oauth-protected-resource/mcp
# 3. in ChatGPT: connect, then write_note + read_note roundtrip; a DIFFERENT GitHub
#    account must be REJECTED by the identity middleware.
# 4. Claude web: connect. If OAuth completes but it never connects and shows an "ofid"
#    reference, that's the known claude.ai client bug (#82/#49) — capture it and stop;
#    it is not a config error on our side.
# 5. backend not public:
curl -m 3 https://<FUNNEL>:8000/   # should NOT reach basic-memory (only 8080 is funneled)
```

## Policy loader
On every `tools/list` the proxy composes the policy reminder under `VAULT_PATH` and
appends it to tool descriptions, so policies are in context when an agent acts.
`memory\policies\Policy Loader.md` supplies the lead-in wording and the list of tools
that carry it; every other `memory\policies\*.md` contributes one line — its title and
the problem stated under its `## Problem` heading. There is no stored index to keep
fresh. The same text goes out as MCP instructions at session start.

Sources are re-read every `POLICY_REFRESH_SECONDS` (optional in `.env`, default 10),
so editing a policy in Obsidian and re-listing tools is a fast loop — no restart, no
reinstall. Adding a policy is dropping a file into the folder; a file without a
`## Problem` section is skipped. `VAULT_PATH` (optional in `.env`, default
`<home>\nimeesh vault`) is the single locator; every path derives from it.

The loader never evaluates content and never blocks or alters any call — it only
appends text to tool descriptions. `proxy.py` holds one hardcoded string,
`Policy Index unavailable — read memory/policies before writing.`, used when the notes
cannot be read: the Index unreadable puts it on the listed tools, the Loader note
unreadable puts it on every tool (nothing is left to name them). Reads and writes
proceed untouched in both cases. To verify without going through OAuth, list the tools
of an unauthenticated proxy carrying the same middleware:
```powershell
# from this folder; single quotes inside — PowerShell 5.1 strips embedded double quotes
.\.venv\Scripts\python.exe -c @'
import asyncio
from fastmcp import Client
from fastmcp.server import create_proxy
import proxy as P

async def main():
    p = create_proxy('http://127.0.0.1:8000/mcp', name='check')
    p.add_middleware(P.CarryPolicyIndex())
    async with Client(p) as c:
        text, carriers = P.policy_payload()
        for t in await c.list_tools():
            if carriers is None or t.name in carriers:
                print(t.name, text.split('\n')[0] in (t.description or ''))

asyncio.run(main())
'@
```
Every tool named in the Loader note should print `True`. Or simply connect from a web
app and inspect `write_note`'s description.

## Security notes
- basic-memory itself has no auth; it is bound to 127.0.0.1 and reachable ONLY through
  the proxy, which enforces GitHub OAuth + the single-user allowlist (fail-closed:
  no token or wrong login → denied).
- Rotate the GitHub client secret if it ever leaks. Never commit `.env`.
- `tailscale funnel off` (or `tailscale funnel 8080 off`) takes the endpoint down fast.

## Troubleshooting

### Web apps say the server is offline, but everything looks fine locally (2026-07-19)
Symptom: ChatGPT reported the connector unreachable, yet `tailscale funnel status` said
"Funnel on", both services were listening, and `curl https://<FUNNEL>/mcp` from this
laptop passed every acceptance test.

Cause: local curls are misleading — inside the tailnet the hostname resolves to the
tailnet IP (100.x) and traffic bypasses the public Funnel ingress. The actual fault was
on Tailscale's side of the fence: the control plane had dropped this node's Funnel
registration (suspected trigger: the laptop's frequent sleep/wake cycles), so the PUBLIC
DNS record reverted from the Funnel ingress IPs to the unroutable 100.x tailnet IP.
External clients could not connect at all.

Diagnose (from this laptop, but bypassing the tailnet path):
```
# Ask an external DNS-over-HTTPS resolver. Healthy = public ingress IPs
# (e.g. 103.84.155.x); broken = 100.x:
node -e "fetch('https://dns.google/resolve?name=lenovoideapad.tailec13e9.ts.net&type=A').then(r=>r.json()).then(x=>console.log(x.Answer))"

# True external vantage. Healthy = "HTTP/1.1 401" + Www-Authenticate
# (that IS success — it's the OAuth entry point). Broken = connection error:
curl "https://api.hackertarget.com/httpheaders/?q=https://lenovoideapad.tailec13e9.ts.net/mcp"
```

Fix — escalation that worked on 2026-07-19 (DNS flipped to ingress IPs within ~3 min
of the serve reset; plain funnel off/on alone did NOT fix it):
```
tailscale down; tailscale up          # fresh control-plane session
tailscale serve reset                 # wipe serve/funnel config
tailscale funnel --bg --https=443 http://127.0.0.1:8080   # re-apply -> re-registers
# then re-run the external DNS check above until it shows public IPs (TTL is 600s,
# so give ChatGPT/Claude up to ~10 min after the flip before retrying)
```

### Watchdog (added 2026-07-20 — this recurs, so it is now automated)
The failure recurred overnight on 2026-07-20 and again after a tailscaled restart, so
the working theory is: any control-plane reconnect (sleep/wake, daemon restart/update)
can silently drop the Funnel DNS registration.

`funnel-watchdog.ps1` (this folder) asks Google DNS over HTTPS for the hostname's
public A record. Do not use `Resolve-DnsName` for this check on Windows: Tailscale's
MagicDNS layer can return the private 100.x address even when `-Server` names a public
or authoritative nameserver. If the external answer has reverted to the tailnet 100.x
IP while Funnel is locally enabled, the watchdog attempts one `tailscale serve reset`
and one Funnel re-apply for that incident. A later run waits through the 10-minute DNS TTL
and verifies the actual invariant: external DNS must contain a public Funnel ingress IP.

A zero exit code from `tailscale funnel` means only that the local daemon accepted the
configuration; it is not recovery. If external DNS is still 100.x after 12 minutes,
the watchdog
logs `UNHEALED`, returns exit code 2 to Task Scheduler, and stops resetting the Funnel.
Repeated resets can conceal a control-plane or tailnet-side failure without repairing
it. Once external DNS becomes public again, the watchdog logs `RECOVERED`, clears
the incident state, and can repair a future incident once.

It logs to `watchdog.log` (gitignored, rotated at 500 KB) and does nothing if Funnel is
intentionally off or the network is down. A non-mutating inspection is available as:

```
powershell -NoProfile -File .\funnel-watchdog.ps1 -StatusOnly
```

Scheduled task `basic-memory-funnel-watchdog` (Task Scheduler, runs as the user,
LeastPrivilege): every 15 min + 2 min after logon + 2 min after wake-from-sleep
(Power-Troubleshooter event 1). Registration needed an elevated shell.
`LastTaskResult = 2` means the local repair was attempted and the public invariant still
failed; inspect the tailnet's Funnel node attribute, HTTPS certificate setting, machine
state, and Tailscale service status rather than repeating the same local reset.

Tailscale auto-update is enabled (`tailscale set --auto-update`, since 1.98.9) — note
each update restarts the daemon, which can itself trigger the drop; the watchdog
catches that too.
