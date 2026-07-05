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

## Security notes
- basic-memory itself has no auth; it is bound to 127.0.0.1 and reachable ONLY through
  the proxy, which enforces GitHub OAuth + the single-user allowlist (fail-closed:
  no token or wrong login → denied).
- Rotate the GitHub client secret if it ever leaks. Never commit `.env`.
- `tailscale funnel off` (or `tailscale funnel 8080 off`) takes the endpoint down fast.
