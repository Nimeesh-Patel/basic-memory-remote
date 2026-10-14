import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from proxy_config import require_loopback_backend, require_loopback_host


class ProxyConfigTests(unittest.TestCase):
    def test_only_native_loopback_addresses_are_accepted(self):
        for host in ("127.0.0.1", "::1"):
            self.assertEqual(require_loopback_host(host), host)
        for host in ("localhost", "0.0.0.0", "192.168.1.1", "example.com"):
            with self.assertRaises(ValueError):
                require_loopback_host(host)

    def test_backend_cannot_escape_to_a_remote_or_credentialed_url(self):
        for url in ("http://127.0.0.1:8000/mcp", "http://[::1]:8000/mcp"):
            self.assertEqual(require_loopback_backend(url), url)
        for url in ("https://example.com/mcp", "http://localhost/mcp",
                    "http://user:secret@127.0.0.1/mcp", "http://127.0.0.1:99999/mcp"):
            with self.assertRaises(ValueError):
                require_loopback_backend(url)


class ProxyPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        memory = Path(cls.temp.name) / "memory"
        memory.mkdir()
        environment = {"BASE_URL": "https://example.invalid", "GH_CLIENT_ID": "fixture",
                       "GH_CLIENT_SECRET": "fixture", "ALLOWED_GITHUB_USER": "fixture",
                       "BASIC_MEMORY_PROJECT_PATH": str(memory), "PROXY_HOST": "127.0.0.1",
                       "BACKEND_URL": "http://127.0.0.1:8000/mcp"}
        with patch.dict(os.environ, environment), \
                patch("fastmcp.server.create_proxy", return_value=Mock()), \
                patch("fastmcp.server.auth.providers.github.GitHubProvider", return_value=Mock()):
            cls.proxy = importlib.import_module("proxy")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_invalid_utf8_is_unavailable_policy_not_silent_success(self):
        path = Path(self.temp.name) / "invalid.md"
        path.write_bytes(b"\xff")
        self.assertIsNone(self.proxy._read(path))

    def test_unexpected_enrichment_failure_still_carries_disclosure(self):
        tool = SimpleNamespace(description="Original", model_copy=lambda update: SimpleNamespace(**update))
        async def next_call(_):
            return [tool]
        with patch.object(self.proxy, "policy_payload", side_effect=ValueError("fixture")):
            results = asyncio.run(self.proxy.CarryPolicyIndex().on_list_tools(None, next_call))
        self.assertIn("Original", results[0].description)
        self.assertIn(self.proxy.POLICY_UNAVAILABLE, results[0].description)

    def test_absent_or_unknown_identity_stays_denied(self):
        from fastmcp.exceptions import AuthorizationError
        for token in (None, SimpleNamespace(claims={}), SimpleNamespace(claims={"login": "other"})):
            with patch.object(self.proxy, "get_access_token", return_value=token):
                with self.assertRaises(AuthorizationError):
                    self.proxy.RequireAllowedUser()._enforce()


if __name__ == "__main__":
    unittest.main()
