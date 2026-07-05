# Basic Memory — cross-app shared memory setup (spec + handoff)

**Purpose of this file:** a self-contained description of what this setup is, why it
exists, and exactly what the desired end-state is on this machine. Point any coding
agent (Codex, Claude Code, etc.) at this file and ask it to **verify the end-state
below and fix anything missing**. A human can also follow it directly.

Machine: Windows 11, user `nimee`. Python 3.13 present.

---

## The problem this solves

The user works on the same intellectual problems across **ChatGPT, Claude (web),
Codex CLI, and Claude Code**. Each app only knows what happened inside its own chat,
so context has to be copied between them by hand. Goal: a **single persistent memory,
shared across all the AI apps**, so progress made in one app is recallable in the others.

Chosen solution: **basic-memory** (https://github.com/basicmachines-co/basic-memory),
an open-source MCP server that stores memory as plain markdown notes the user can also
read/edit in Obsidian.

## Key architecture decisions (IMPORTANT — do not regress these)

- **basic-memory is scoped to a SUBFOLDER, not the whole vault.** Project `memory`
  points at `C:\Users\nimee\nimeesh vault\memory`. bm only ever reads/writes/indexes
  that folder. It must NOT be pointed at the vault root.
  - *Why:* when bm was first pointed at the whole vault, it REWROTE all 677 existing
    notes (added `permalink:` frontmatter, and `title:`/`type:` to notes that had none).
    Scoping it to a subfolder makes it structurally impossible to touch the rest of the
    vault again. A subfolder is still the SAME Obsidian vault, just one folder.
- **The rest of the vault reaches bm only via "perspirator" (the `perspirate` skill),**
  which has full-vault scope and acts as a curating bridge: it reads the wider vault and
  writes relevant context into the `memory` folder. bm itself never sees the other notes.
  (Built as Perspirator Modes 7/8 — see bottom.)
- **New memory notes go in the `memory` folder** — `write_note` with `folder: "."`
  (bm's project root IS the memory folder).
- **Web apps (ChatGPT, Claude web) connect remotely** — via Tailscale Funnel + OAuth
  proxy; see `RUNBOOK.md` in this repo.

## History / incident log

- 2026-06-29: installed; initially (mistakenly) scoped to the whole vault; bm rewrote all
  677 notes adding `permalink` (+ some `title`/`type`).
- 2026-06-30: error-corrected. Re-scoped bm to the `memory` subfolder; reset the index DB;
  stripped the `permalink` line from all 677 notes (left all other metadata, including the
  `title`/`type` bm had added, untouched per the user's instruction).
- Full pre-revert backup of every .md file: `C:\Users\nimee\nimeesh-vault-backup-20260630_174333`.

---

## Desired end-state (verify each item; fix if missing)

1. **basic-memory installed and on PATH.**
   - Check: `basic-memory --version` -> `0.22.x`+. Exe: `C:\Users\nimee\.local\bin\basic-memory.exe`.
   - If missing: `python -m pip install uv` then `python -m uv tool install basic-memory`.

2. **Project `memory` points at the SUBFOLDER and is default — and it is the ONLY
   project that points anywhere inside the vault.**
   - Check: `basic-memory project list` shows `memory` ->
     `C:\Users\nimee\nimeesh vault\memory`, default. NO project may point at the vault root.
   - If wrong: `basic-memory reset` (clears index), then
     `basic-memory project add memory "C:\Users\nimee\nimeesh vault\memory"` and
     `basic-memory project default memory`. (CLI reads/writes both the DB and
     `C:\Users\nimee\.basic-memory\config.json`; if they disagree, the DB wins for
     `project list` — reconcile by emptying `projects` in config.json then re-adding.)

3. **No `permalink:` frontmatter in the vault outside the `memory` folder.**
   - Check (PowerShell): notes under `nimeesh vault` excluding `\memory\` must contain no
     `permalink:` lines. If any exist, bm has been re-indexing the root — fix item 2 first,
     then strip them again.

4. **Registered as a local stdio MCP server in Codex.**
   - Check: `codex mcp list` shows `basic-memory`. Config in `C:\Users\nimee\.codex\config.toml`:
     ```toml
     [mcp_servers.basic-memory]
     command = 'C:\Users\nimee\.local\bin\basic-memory.exe'
     args = ["mcp"]
     [mcp_servers.basic-memory.env]
     PYTHONIOENCODING = "utf-8"
     PYTHONUTF8 = "1"
     ```
   - If missing: `codex mcp add basic-memory --env PYTHONUTF8=1 --env PYTHONIOENCODING=utf-8 -- "C:\Users\nimee\.local\bin\basic-memory.exe" mcp`
   - The env vars are REQUIRED on Windows (cp1252 console crashes on unicode otherwise).

5. **Registered as a local stdio MCP server in Claude Code.**
   - Check: `claude mcp list` shows `basic-memory: ... Connected`.
   - If missing: `claude mcp add basic-memory -s user -e PYTHONUTF8=1 -e PYTHONIOENCODING=utf-8 -- "C:\Users\nimee\.local\bin\basic-memory.exe" mcp`

6. **Memory-usage protocol present for both agents.**
   - `C:\Users\nimee\.claude\CLAUDE.md` and `C:\Users\nimee\.codex\AGENTS.md` each contain a
     "Shared cross-app memory (basic-memory)" section: recall before working, record progress,
     prune stale notes, and write new notes with `folder: "."` (the `memory` folder).

---

## How an agent should USE the memory (behavior contract)

When working on one of the user's ongoing problems/research/projects (not throwaway tasks):
- **Recall first:** `search_notes`, `recent_activity`, `build_context`.
- **Record progress:** `write_note` (folder ".") on a decision/insight/durable fact.
- **Prune:** `edit_note` / `delete_note` to keep memory a useful working set, not a transcript.

---

## DONE (2026-07-05): perspirator as the vault->memory bridge

Built as Modes 7/8 of the Perspirator 9000 skill (deployed to `~/.claude/commands`):
- **Mode 7** ("brief memory on <problem>"): vault -> memory. Curates the relevant vault
  neighbourhood into a brief in `nimeesh vault\memory` for the other apps.
- **Mode 8** ("promote memory"): memory -> vault. Promotes durable knowledge from a memory
  note into proper problem-notes (dedup'd, additive).
- `problem_index.py` excludes `/memory` so the two note populations never double-index.
- perspirate is LOCAL-only (Claude Code); web apps just consume whatever memory notes exist.

## DONE (2026-07-05): connect the web apps (ChatGPT + Claude web)

Self-hosted (free): Tailscale Funnel -> FastMCP OAuth proxy (GitHub IdP, single-user
allowlist) -> local basic-memory on loopback. Live and acceptance-tested — **see
`RUNBOOK.md` in this repo** for the architecture, procedure, live values, and tests
(single source of truth; not repeated here).
