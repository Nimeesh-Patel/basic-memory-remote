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
  POLICY_INDEX_PATH   default <home>\nimeesh vault\memory\policies\Policy Index.md
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

# Policy Index: a locator, not a policy. POLICY_INDEX_PATH overrides where the
# Index is read from; nothing else about this loader is configurable.
POLICY_INDEX_PATH = Path(
    os.environ.get(
        "POLICY_INDEX_PATH",
        Path.home() / "nimeesh vault" / "memory" / "policies" / "Policy Index.md",
    )
)
POLICY_INDEX_TTL_SECONDS = 120

WRITE_CLASS_TOOLS = frozenset(
    {"write_note", "edit_note", "delete_note", "move_note", "canvas"}
)
POLICY_LEAD_IN = (
    "Active policies — read the relevant ones (memory/policies) before writing:"
)
POLICY_UNAVAILABLE = "Policy Index unavailable — read memory/policies before writing."

_policy_cache: tuple[float, str] | None = None


def policy_text() -> str:
    """The Policy Index text, read fresh at most every POLICY_INDEX_TTL_SECONDS.

    Transport only: the file's bytes are read and returned. Nothing here parses,
    evaluates, or judges what the Index says. If it cannot be read, callers get
    the disclosure line instead.
    """
    global _policy_cache
    now = time.monotonic()
    if _policy_cache is not None and now - _policy_cache[0] < POLICY_INDEX_TTL_SECONDS:
        return _policy_cache[1]
    try:
        text = f"{POLICY_LEAD_IN}\n\n{POLICY_INDEX_PATH.read_text(encoding='utf-8')}"
    except OSError:
        text = POLICY_UNAVAILABLE
    _policy_cache = (now, text)
    return text


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
    """Append the Policy Index to write-class tool descriptions on tools/list.

    Policies in memory\\policies\\ only exert force if they are in context at the
    moment of action, so the Index rides along with the tools that write. What
    it says — and which policies the agent then reads in full — is the agent's
    judgment, not this code's. It carries the Index rather than a fixed list of
    policies, so adding or changing a policy needs no code change here.

    This hook is additive and fail-open: it touches descriptions only, and any
    failure returns the tools untouched. It never blocks or alters a call.
    """

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        tools = await call_next(context)
        try:
            text = policy_text()
            return [
                tool.model_copy(
                    update={
                        "description": f"{tool.description or ''}\n\n{text}".strip()
                    }
                )
                if tool.name in WRITE_CLASS_TOOLS
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
    instructions=policy_text(),
)
proxy.add_middleware(RequireAllowedUser())
proxy.add_middleware(CarryPolicyIndex())

if __name__ == "__main__":
    print(f"basic-memory-remote proxy -> backend {BACKEND_URL}")
    print(f"  public base_url: {BASE_URL}")
    print(f"  allowed GitHub user: {ALLOWED_GITHUB_USER}")
    print(f"  listening on http://{PROXY_HOST}:{PROXY_PORT}/mcp")
    proxy.run(transport="http", host=PROXY_HOST, port=PROXY_PORT)
