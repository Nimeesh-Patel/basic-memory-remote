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

Every text this proxy shows an agent lives in a vault note under VAULT_PATH:
memory\policies\Policy Loader.md (the lead-in wording and the tools that carry
it) and memory\policies\Policy Index.md. The single exception, which cannot be
otherwise, is the hardcoded disclosure used when those notes are unreadable.
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

POLICY_INDEX_NOTE = VAULT_PATH / "memory" / "policies" / "Policy Index.md"
POLICY_LOADER_NOTE = VAULT_PATH / "memory" / "policies" / "Policy Loader.md"

# Section headings in the Loader note: locators, not content.
LEAD_IN_HEADING = "## Lead-in"
TOOLS_HEADING = "## Tools that carry it"

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


def policy_payload() -> tuple[str, frozenset[str] | None]:
    """The text to append and the tools that carry it, refreshed periodically.

    Transport only: both the lead-in wording and the list of tools are read
    from the Policy Loader note, and the Policy Index is read whole. Nothing
    here parses, evaluates, or judges what any of them say. A tool set of None
    means the note layer could not be read, so the disclosure goes everywhere.
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
        index = _read(POLICY_INDEX_NOTE)
        if not lead_in or not tools:
            payload = (POLICY_UNAVAILABLE, None)
        elif index is None:
            payload = (POLICY_UNAVAILABLE, frozenset(tools))
        else:
            payload = (f"{lead_in}\n\n{index}", frozenset(tools))

    _policy_cache = (now, payload)
    return payload


class RequireAllowedUser(Middleware):
    """Fail-closed: only ALLOWED_GITHUB_USER may do anything."""

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

if __name__ == "__main__":
    print(f"basic-memory-remote proxy -> backend {BACKEND_URL}")
    print(f"  public base_url: {BASE_URL}")
    print(f"  allowed GitHub user: {ALLOWED_GITHUB_USER}")
    print(f"  listening on http://{PROXY_HOST}:{PROXY_PORT}/mcp")
    proxy.run(transport="http", host=PROXY_HOST, port=PROXY_PORT)
