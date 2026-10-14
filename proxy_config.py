"""Deterministic binding checks; the remote URL is owned by OAuth configuration."""

from ipaddress import ip_address
from urllib.parse import urlsplit


def require_loopback_host(host: str) -> str:
    try:
        address = ip_address(host)
    except ValueError as error:
        raise ValueError("proxy host must be a literal loopback address") from error
    if not address.is_loopback:
        raise ValueError("proxy host must remain loopback-only; Funnel owns exposure")
    return host


def require_loopback_backend(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.username is not None
            or parsed.password is not None or parsed.fragment):
        raise ValueError("backend must be an unauthenticated loopback HTTP URL")
    require_loopback_host(parsed.hostname or "")
    # Accessing port also validates malformed/out-of-range port syntax.
    parsed.port
    return url
