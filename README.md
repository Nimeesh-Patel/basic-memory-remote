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
only through **Perspirator**, which has full-vault scope and moves curated
context into `memory\` and durable knowledge back out, under the approval rules
its runtime defines. bm itself never indexes the vault outside `memory\` (it
once did, and rewrote every note — see the incident log below for why this
boundary is load-bearing).

Since 2026-07-10, Perspirator's operating logic is itself a vault note:
`memory\perspirator\Perspirator.md`, with its bootstrap contract, proposals,
and run reports alongside. The installed skill is only a locator that loads
them. Because `memory\perspirator\` sits inside bm's scope, every connected
app can read — and criticise — Perspirator's logic and run reports through bm;
execution still happens only in the local CLIs.

## Key architecture decisions (do not regress these)

- **bm is scoped to a SUBFOLDER, not the whole vault.** Project `memory` points
  at `C:\Users\nimee\nimeesh vault\memory`. It must NOT be pointed at the vault
  root. *Why:* when bm was first pointed at the whole vault it REWROTE all 677
  existing notes, adding `permalink:` frontmatter (and `title:`/`type:` to notes
  that had none). Scoping it to a subfolder makes it structurally impossible to
  touch the rest of the vault again. A subfolder is still the same Obsidian
  vault, just one folder.
- **New memory notes go in the `memory` folder** — `write_note` with
  `folder: "."`, since bm's project root *is* the memory folder.
- **Local CLIs speak stdio; web apps connect remotely** via Tailscale Funnel +
  the OAuth proxy (`RUNBOOK.md`). bm has no auth of its own, so it stays
  loopback-only.
- **Every text an agent reads is a vault note; code holds only locators.** The
  proxy carries policy text (below); Perspirator's bootstrap contract and
  runtime are notes, not packaged files.

Incident log: 2026-06-29 installed, mistakenly scoped to the whole vault, 677
notes rewritten. 2026-06-30 error-corrected — re-scoped to `memory\`, index DB
reset, `permalink:` stripped from all 677 notes (other metadata left as bm had
written it, per instruction). Full pre-revert backup of every `.md`:
`C:\Users\nimee\nimeesh-vault-backup-20260630_174333`.

## Policy loader

Policies in `memory\policies\` only exert force if they are in an agent's
context when it acts, so the proxy carries them there. On every `tools/list` it
reads two notes and appends their text to tool descriptions:

| Source | Supplies |
|---|---|
| `memory\policies\Policy Loader.md` | the lead-in wording, and the list of tools that carry the reminder |
| every `memory\policies\*.md` with a `## Problem` section | one line each: the policy's title and the problem it states it solves |

**There is no stored index.** Each policy states the one problem it solves, and
the selection surface is composed from those statements at read time, so
changing a policy's problem changes what agents see on the next read and adding
a policy is dropping in a file. Nothing has to be kept in sync, which is the
point: a hand-maintained index is a copy that goes stale exactly when nobody
remembers to update it. A file with no `## Problem` section is not a policy and
is skipped.

The same text is also sent as the server's MCP instructions at session start.
The wording and the choice of tools are decisions, so both live in a note
rather than in `proxy.py`: rewording the reminder or changing which tools carry
it is a vault edit with **no code change and no restart** — sources are re-read
every `POLICY_REFRESH_SECONDS` (default 10). `VAULT_PATH` is the single locator
the proxy needs (default `<home>\nimeesh vault`); every path derives from it.

The loader never evaluates content and never blocks or alters a call: it
appends text to descriptions and nothing else. `proxy.py` holds exactly one
string of instruction text — `Policy Index unavailable — read memory/policies
before writing.` — used when the notes themselves cannot be read, which is the
one thing a note cannot say about itself. If no policy can be read, the listed
tools carry that disclosure; if the Loader note is unreadable, every tool does,
since the note naming them is gone. Either way every read and write
still succeeds untouched. Note that the local CLIs reach basic-memory over
stdio and bypass the proxy, so this loader does not cover them — there, policy
loading is the Perspirator runtime's job.

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
| `README.md` | This overview — the map of the whole system, and the local half: bm scoped to `vault\memory`, registered in Claude Code + Codex. |
| `RUNBOOK.md` | The **remote** half: Funnel + GitHub OAuth App + proxy, step by step, with acceptance tests and the current live deployment values. |
| `proxy.py` | The FastMCP OAuth proxy (GitHub provider + fail-closed `RequireAllowedUser` middleware, plus the `CarryPolicyIndex` loader). Config via `.env`; no secrets in code. |
| `start.ps1` | Starts both services: bm on `127.0.0.1:8000`, proxy on `127.0.0.1:8080`. |
| `.env.example` | Template for `.env` (gitignored): Funnel URL, GitHub OAuth creds, allowed user. |

Not in this repo but part of the system:

| Piece | Where |
|-------|-------|
| Memory notes | `C:\Users\nimee\nimeesh vault\memory\` |
| bm CLI + config | `C:\Users\nimee\.local\bin\basic-memory.exe`, `C:\Users\nimee\.basic-memory\config.json` |
| Perspirator runtime + bootstrap contract | `...\memory\perspirator\Perspirator.md` and `Bootstrap.md` — canonical, edited in Obsidian, visible to all apps via bm |
| Policy text the proxy carries | `...\memory\policies\` — one note per policy, each stating its problem |
| Perspirator installer + structural scripts | The [Perspirator 9000](https://github.com/Nimeesh-Patel/Perspirator-9000) repo / `C:\Users\nimee\Perspirator 9000`, deployed to `~\.claude\commands` and `~\.agents\skills\perspirate` |
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

1. **Local bm.** `python -m pip install uv` then `python -m uv tool install
   basic-memory` (exe lands at `~\.local\bin\basic-memory.exe`). Scope it to the
   subfolder — and to nothing else:
   ```
   basic-memory project add memory "C:\Users\nimee\nimeesh vault\memory"
   basic-memory project default memory
   ```
   Verify with `basic-memory project list`: `memory` must be default and no
   project may point at the vault root. (The CLI writes both the index DB and
   `~\.basic-memory\config.json`; if they disagree the DB wins for
   `project list` — reconcile by emptying `projects` in config.json and
   re-adding.) Then confirm no `permalink:` frontmatter exists in the vault
   outside `memory\`; if any appears, bm has been re-indexing the root.
2. **Register it with the local CLIs** (the env vars are REQUIRED on Windows —
   a cp1252 console crashes on unicode otherwise):
   ```
   claude mcp add basic-memory -s user -e PYTHONUTF8=1 -e PYTHONIOENCODING=utf-8 -- "C:\Users\nimee\.local\bin\basic-memory.exe" mcp
   codex  mcp add basic-memory   --env PYTHONUTF8=1 --env PYTHONIOENCODING=utf-8 -- "C:\Users\nimee\.local\bin\basic-memory.exe" mcp
   ```
   Check with `claude mcp list` / `codex mcp list`.
3. `RUNBOOK.md` — the remote path: Tailscale, GitHub OAuth App, `.env`,
   `start.ps1`, connector registration, acceptance tests.

How agents are told to *use* the memory is not documented here: the recall and
pruning protocol lives in `~\.claude\CLAUDE.md` / `~\.codex\AGENTS.md`, and the
policies the proxy carries live in `memory\policies\`.

## Security model (summary)

Unauthenticated → 401 + OAuth discovery. Authenticated but not
`ALLOWED_GITHUB_USER` → denied (fail-closed middleware on every request and
every tool call). bm is never publicly reachable — only the proxy port is
funneled. Internet scanners hitting the public URL get 404/401 noise; nothing
reaches memory without a GitHub token for the allowlisted account. Secrets
live only in `.env` (gitignored). If this repo is made public, the Funnel URL
and GitHub username in RUNBOOK.md become known — the endpoint is still
auth-gated, but prefer a private repo.
