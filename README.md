# basic-memory-remote — one memory, every AI app

Infrastructure and documentation for a **single persistent memory shared across
ChatGPT, Claude (web), Codex CLI, and Claude Code**, built on
[basic-memory](https://github.com/basicmachines-co/basic-memory) (bm). Memory
notes are plain markdown in a subfolder of an Obsidian vault — readable and
editable by hand, by any of the AI apps, or by Obsidian itself.

This repo is the **single source of truth for the system's infrastructure**:
how it's wired, why it's wired that way, how to operate it, and how to rebuild
it from scratch. The *content* — the memory notes themselves, and since
2026-07-10 the Perspirator runtime that curates them — lives in the vault's
`memory\` folder, not here.

## The whole system

```
                        C:\Users\nimee\nimeesh vault          (Obsidian vault)
                        └── memory\                           (the ONLY folder bm touches)
                                 ▲
              ┌──────────────────┼──────────────────────┐
              │ stdio MCP        │ stdio MCP            │ HTTP MCP (loopback)
        Claude Code           Codex CLI            basic-memory  127.0.0.1:8000
        (local)               (local)                   ▲
                                                        │
                                          FastMCP OAuth proxy  127.0.0.1:8080
                                          (GitHub IdP, single-user allowlist)
                                                        ▲
                                          Tailscale Funnel (stable ts.net URL)
                                                        ▲
                                            ChatGPT  /  Claude web
```

Plus a curation layer: the rest of the vault (677+ problem notes) is reachable
only through **Perspirator**'s bridge modes — Mode 7 exports a curated brief
from the vault into `memory\`; Mode 8 promotes durable knowledge from `memory\`
back into vault problem notes. bm itself never indexes the vault outside
`memory\` (it once did, and rewrote every note — see SETUP.md's incident log
for why this boundary is load-bearing).

Since 2026-07-10, Perspirator's operating logic is itself a vault note:
`memory\perspirator\Perspirator.md`, with its changelog, proposals, behavioural
cases, and run reports alongside. The installed skill is only a bootstrap that
loads it. Because `memory\perspirator\` sits inside bm's scope, every connected
app can read — and criticise — Perspirator's logic and run reports through bm;
execution still happens only in the local CLIs.

## Why this shape

- **bm scoped to a subfolder, not the vault** — makes it structurally
  impossible for bm to touch the other notes again.
- **Web apps need a public OAuth 2.1 MCP server** — they cannot speak to a
  local process, and they don't support static bearer tokens. Hence
  Funnel + OAuth proxy rather than just running bm.
- **Tailscale Funnel over Cloudflare/ngrok** — free, a stable URL that
  survives reboots, and no auth layer of its own to fight (Cloudflare Access
  is not connector-traversable: anthropics/claude-ai-mcp #410).
- **GitHub as identity provider** — the proxy delegates login to GitHub and
  then allowlists exactly one login (`ALLOWED_GITHUB_USER`), fail-closed. bm
  itself has no auth, so it stays loopback-only; the proxy is the only door.

## What's in this repo

| File | Role |
|------|------|
| `README.md` | This overview — the map of the whole system. |
| `SETUP.md` | The **local** half: install bm, scope it to `vault\memory`, register it in Claude Code + Codex, memory-usage protocol. Written as a verifiable end-state a coding agent can be pointed at. |
| `RUNBOOK.md` | The **remote** half: Funnel + GitHub OAuth App + proxy, step by step, with acceptance tests and the current live deployment values. |
| `proxy.py` | The FastMCP OAuth proxy (GitHub provider + fail-closed `RequireAllowedUser` middleware). Config via `.env`; no secrets in code. |
| `start.ps1` | Starts both services: bm on `127.0.0.1:8000`, proxy on `127.0.0.1:8080`. |
| `.env.example` | Template for `.env` (gitignored): Funnel URL, GitHub OAuth creds, allowed user. |

Not in this repo but part of the system:

| Piece | Where |
|-------|-------|
| Memory notes | `C:\Users\nimee\nimeesh vault\memory\` |
| bm CLI + config | `C:\Users\nimee\.local\bin\basic-memory.exe`, `C:\Users\nimee\.basic-memory\config.json` |
| Perspirator runtime (all modes, incl. bridge Modes 7/8) | `C:\Users\nimee\nimeesh vault\memory\perspirator\Perspirator.md` — canonical, edited in Obsidian, visible to all apps via bm |
| Perspirator bootstrap + structural scripts | The [Perspirator 9000](https://github.com/Nimeesh-Patel/Perspirator-9000) repo / `C:\Users\nimee\Perspirator 9000`, deployed to `~\.claude\commands` and `~\.agents\skills\perspirate` |
| Agent memory protocol | `~\.claude\CLAUDE.md` and `~\.codex\AGENTS.md` ("Shared cross-app memory" section) |
| Auto-start at logon | `basic-memory-remote.cmd` in the user Startup folder (`shell:startup`) → runs `start.ps1` |

## Operating it

- **Is it up?** `Get-Process basic-memory, python` (both should exist);
  `tailscale funnel status`. Logs: `bm.*.log`, `proxy.*.log` here (gitignored).
- **Restart:** `.\start.ps1` (kill the two processes first if half-alive).
- **Kill switch (public endpoint off, fast):** `tailscale funnel --https=443 off`.
  Local CLIs keep working; only the web apps lose access.
- **Rotate the GitHub client secret:** GitHub → Settings → Developer settings →
  the OAuth app → generate new secret → update `.env` → restart.
- **If the Funnel URL ever changes** (rename machine/tailnet): update the
  GitHub OAuth App's callback URL, `BASE_URL` in `.env`, restart, and
  re-add the connector in each web app.
- **Connector not working in a chat?** The server side is verifiable in
  seconds: `curl -i https://<funnel>/mcp` must return 401 with a
  `WWW-Authenticate` header. If it does, the problem is app-side (usually the
  connector isn't enabled in that conversation).

## Rebuilding from scratch

1. `SETUP.md` — local bm, scoped correctly, wired into the CLIs. Point a
   coding agent at it and ask it to verify/fix the end-state.
2. `RUNBOOK.md` — the remote path: Tailscale, GitHub OAuth App, `.env`,
   `start.ps1`, connector registration, acceptance tests.

## Security model (summary)

Unauthenticated → 401 + OAuth discovery. Authenticated but not
`ALLOWED_GITHUB_USER` → denied (fail-closed middleware on every request and
every tool call). bm is never publicly reachable — only the proxy port is
funneled. Internet scanners hitting the public URL get 404/401 noise; nothing
reaches memory without a GitHub token for the allowlisted account. Secrets
live only in `.env` (gitignored). If this repo is made public, the Funnel URL
and GitHub username in RUNBOOK.md become known — the endpoint is still
auth-gated, but prefer a private repo.
