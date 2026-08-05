r"""
FastMCP OAuth proxy in front of a local basic-memory server.

  [ChatGPT / Claude web] --HTTPS--> Tailscale Funnel
      --> this proxy (127.0.0.1:8080, OAuth 2.1 via GitHub)
      --> basic-memory (127.0.0.1:8000, loopback only)

Security model:
  - Unauthenticated requests get a 401 + WWW-Authenticate from the GitHub
    OAuth provider (standard MCP OAuth 2.1 discovery flow).
  - Authenticated requests are additionally gated by a fail-closed identity
    middleware: only ALLOWED_GITHUB_USER passes. Any other GitHub login is
    rejected, and a missing/unreadable token is rejected too.

Config comes from environment (or a .env file next to this script). NEVER
put secrets in this file.
  BASE_URL            https://<machine>.<tailnet>.ts.net   (the Funnel URL)
  GH_CLIENT_ID        GitHub OAuth App client id
  GH_CLIENT_SECRET    GitHub OAuth App client secret
  ALLOWED_GITHUB_USER your GitHub login (exact, case-insensitive match)
  BACKEND_URL         default http://127.0.0.1:8000/mcp
  PROXY_HOST          default 127.0.0.1
  PROXY_PORT          default 8080
  VAULT_PATH          default <home>\nimeesh vault   (the only note locator)
  POLICY_REFRESH_SECONDS  default 10

Every text this proxy shows an agent lives in a vault note under VAULT_PATH.
memory\policies\Policy Loader.md supplies the lead-in wording and the tools
that carry it; the selection surface is composed at read time from the
`## Problem` section of every policy in memory\policies\, so no stored index
has to be kept in sync. The single exception, which cannot be otherwise, is
the hardcoded disclosure used when those notes are unreadable.
"""

import os
import sys
import time
from pathlib import Path

from fastmcp.server import create_proxy
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.exceptions import AuthorizationError


def load_dotenv(path: Path) -> None:
    """Tiny .env loader (no dependency). KEY=VALUE lines; # comments."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


load_dotenv(Path(__file__).with_name(".env"))


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(f"FATAL: missing required env var {name} (set it in .env)")
    return val


BASE_URL = _require("BASE_URL").rstrip("/")
GH_CLIENT_ID = _require("GH_CLIENT_ID")
GH_CLIENT_SECRET = _require("GH_CLIENT_SECRET")
ALLOWED_GITHUB_USER = _require("ALLOWED_GITHUB_USER").lower()
BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000/mcp")
PROXY_HOST = os.environ.get("PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8080"))

# One locator. Every text this program shows an agent lives in a vault note
# below it; nothing here holds instruction text except the disclosure below.
VAULT_PATH = Path(os.environ.get("VAULT_PATH", Path.home() / "nimeesh vault"))
POLICY_REFRESH_SECONDS = float(os.environ.get("POLICY_REFRESH_SECONDS", "10"))

POLICIES_DIR = VAULT_PATH / "memory" / "policies"
POLICY_LOADER_NOTE = POLICIES_DIR / "Policy Loader.md"

# Section headings: locators, not content.
LEAD_IN_HEADING = "## Lead-in"
TOOLS_HEADING = "## Tools that carry it"
PROBLEM_HEADING = "## Problem"

# The one hardcoded string this program is allowed. It cannot live in a note,
# because it is what an agent must be told when the note layer is unreachable.
POLICY_UNAVAILABLE = "Policy Index unavailable — read memory/policies before writing."

_policy_cache: tuple[float, tuple[str, frozenset[str] | None]] | None = None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _section_body(text: str, heading: str) -> str:
    """The lines under a `## ` heading, up to the next `## ` heading."""
    body, collecting = [], False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            collecting = line.strip() == heading
            continue
        if collecting:
            body.append(line)
    return "\n".join(body).strip()


def _bullets(body: str) -> list[str]:
    return [
        line.strip()[2:].strip()
        for line in body.split("\n")
        if line.strip().startswith("- ")
    ]


def _title(text: str, fallback: str) -> str:
    """The frontmatter title, else the filename."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if line.startswith("title:"):
                return line.partition(":")[2].strip().strip("'\"") or fallback
    return fallback


def _load_surface_owner():
    """Import Perspirator's `policy_index`, the one owner of this rule.

    The Policy Loader note states the contract for *both* routes: only
    `status: active`, `type: policy` notes, and malformed active policies are
    refused. This file used to answer that question a second time, and the two
    answers had drifted apart in both directions — a superseded or draft policy
    was served here as if current, while the local route refused the whole
    surface over a single malformed file. One rule, one implementation.
    """
    for candidate in filter(None, [
            os.environ.get("PERSPIRATOR_TOOLS"),
            Path.home() / ".claude" / "commands",
            Path.home() / ".agents" / "skills" / "perspirate"]):
        directory = Path(candidate)
        if not (directory / "policy_index.py").is_file():
            continue
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
        try:
            from policy_index import active_policy_surface
            return active_policy_surface
        except ImportError:
            continue
    return None


def policy_problems() -> str | None:
    """One line per active policy: its title and the problem it says it solves.

    Transport only: the problem text is copied, never interpreted. Returning
    None means the surface could not be established, and the caller then sends
    the disclosure. Serving an unfiltered list would be worse than serving
    nothing, because a retired policy presented as current is a wrong
    instruction rather than a missing one.
    """
    surface = _load_surface_owner()
    if surface is None:
        return None
    try:
        records = surface(VAULT_PATH)
    except (ValueError, OSError):
        # A malformed active policy is refused as a whole surface, exactly as
        # the local route refuses it. Ambiguity here is not a partial answer.
        return None
    lines = [
        f"- {record['title']} — {' '.join(record['problem'].split())}"
        for record in records
    ]
    return "\n".join(lines) if lines else None


def policy_payload() -> tuple[str, frozenset[str] | None]:
    """The text to append and the tools that carry it, refreshed periodically.

    Transport only: the lead-in wording and the list of tools come from the
    Policy Loader note, and the selection surface is composed from each
    policy's own stated problem. Nothing here parses, evaluates, or judges what
    any of them say. A tool set of None means the note layer could not be read,
    so the disclosure goes everywhere.
    """
    global _policy_cache
    now = time.monotonic()
    if _policy_cache is not None and now - _policy_cache[0] < POLICY_REFRESH_SECONDS:
        return _policy_cache[1]

    loader = _read(POLICY_LOADER_NOTE)
    if loader is None:
        payload = (POLICY_UNAVAILABLE, None)
    else:
        lead_in = _section_body(loader, LEAD_IN_HEADING)
        tools = _bullets(_section_body(loader, TOOLS_HEADING))
        problems = policy_problems()
        if not lead_in or not tools:
            payload = (POLICY_UNAVAILABLE, None)
        elif problems is None:
            payload = (POLICY_UNAVAILABLE, frozenset(tools))
        else:
            payload = (f"{lead_in}\n\n{problems}", frozenset(tools))

    _policy_cache = (now, payload)
    return payload


class RequireAllowedUser(Middleware):
    """Fail-closed: only ALLOWED_GITHUB_USER may issue a request.

    FastMCP dispatches `on_message` -> `on_request` / `on_notification` ->
    the method-specific hook, so gating `on_request` covers every request the
    protocol has: initialize, tools/list, tools/call, resources/read,
    resources/list, prompts/get and prompts/list. Every path that can read or
    write memory is a request, so that is the whole data surface.

    Notifications are deliberately not gated. They carry no data access —
    cancellation, progress, initialized — and a notification has no response,
    so raising from one would be an error with nowhere to go.

    `on_call_tool` is a deliberate second gate on the one hook that executes
    something, kept so a future change to request dispatch cannot silently open
    the highest-consequence path. It is redundant on purpose, which is
    different from redundancy nobody chose.
    """

    def _enforce(self) -> None:
        token = get_access_token()
        if token is None:
            raise AuthorizationError("Authentication required.")
        claims = getattr(token, "claims", None) or {}
        login = str(claims.get("login", "")).lower()
        if not login or login != ALLOWED_GITHUB_USER:
            raise AuthorizationError(f"Access denied for GitHub user: {login or '<unknown>'}")

    async def on_request(self, context: MiddlewareContext, call_next):
        self._enforce()
        return await call_next(context)

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        self._enforce()
        return await call_next(context)


class CarryPolicyIndex(Middleware):
    """Append the Policy Index to tool descriptions on tools/list.

    Policies in memory\\policies\\ only exert force if they are in context at the
    moment of action, so the Index rides along with the tools that act on
    memory. Which tools those are, and what the reminder says, are read from
    the Policy Loader note — both are decisions, so neither lives here. What
    the policies mean, and which of them the agent then reads in full, is the
    agent's judgment, not this code's.

    This hook is additive and fail-open: it touches descriptions only, and any
    failure returns the tools untouched. It never blocks or alters a call.
    """

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        tools = await call_next(context)
        try:
            text, carriers = policy_payload()
            return [
                tool.model_copy(
                    update={
                        "description": f"{tool.description or ''}\n\n{text}".strip()
                    }
                )
                if carriers is None or tool.name in carriers
                else tool
                for tool in tools
            ]
        except Exception:
            return tools


auth = GitHubProvider(
    client_id=GH_CLIENT_ID,
    client_secret=GH_CLIENT_SECRET,
    base_url=BASE_URL,
    required_scopes=["read:user"],
)

proxy = create_proxy(
    BACKEND_URL,
    name="basic-memory-remote",
    auth=auth,
    # Server instructions are sent once at session start; the middleware keeps
    # the same text fresh on every tools/list.
    instructions=policy_payload()[0],
)
proxy.add_middleware(RequireAllowedUser())
proxy.add_middleware(CarryPolicyIndex())

def policy_status() -> tuple[bool, str]:
    """Whether the Policy Index is actually being carried, and why not.

    This program loads meaning owned by a Markdown note, and until 2026-08-05
    it did so without ever saying whether the load succeeded: an edit that
    removed `## Lead-in` and repurposed `## Tools that carry it` silently
    replaced the Index with a disclosure for eight days, and nothing reported
    it. Loading Markdown-owned theory is only half the contract; the other half
    is refusing quietly to pretend it worked.
    """
    text, carriers = policy_payload()
    if text == POLICY_UNAVAILABLE:
        if _read(POLICY_LOADER_NOTE) is None:
            return False, f"cannot read {POLICY_LOADER_NOTE}"
        loader = _read(POLICY_LOADER_NOTE) or ""
        missing = [
            heading for heading in (LEAD_IN_HEADING, TOOLS_HEADING)
            if not _section_body(loader, heading)
        ]
        if missing:
            return False, f"{POLICY_LOADER_NOTE.name} has no {' and no '.join(missing)}"
        return False, "no active policy surface (a malformed active policy, or none)"
    if not carriers:
        return False, f"{TOOLS_HEADING} named no tools"
    return True, f"{len(text.splitlines()) - 2} policies on {len(carriers)} tools"


if __name__ == "__main__":
    healthy, detail = policy_status()
    if "--check" in sys.argv:
        print(f"policy index: {'ok' if healthy else 'DEGRADED'} — {detail}")
        sys.exit(0 if healthy else 1)
    print(f"basic-memory-remote proxy -> backend {BACKEND_URL}")
    print(f"  public base_url: {BASE_URL}")
    print(f"  allowed GitHub user: {ALLOWED_GITHUB_USER}")
    print(f"  policy index: {'ok' if healthy else 'DEGRADED'} — {detail}")
    print(f"  listening on http://{PROXY_HOST}:{PROXY_PORT}/mcp")
    proxy.run(transport="http", host=PROXY_HOST, port=PROXY_PORT)
