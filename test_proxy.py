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
            results = asyncio.run(self.proxy.CarryPolicyReminder().on_list_tools(None, next_call))
        self.assertIn("Original", results[0].description)
        self.assertIn(self.proxy.POLICY_UNAVAILABLE, results[0].description)

    def test_absent_or_unknown_identity_stays_denied(self):
        from fastmcp.exceptions import AuthorizationError
        for token in (None, SimpleNamespace(claims={}), SimpleNamespace(claims={"login": "other"})):
            with patch.object(self.proxy, "get_access_token", return_value=token):
                with self.assertRaises(AuthorizationError):
                    self.proxy.RequireAllowedUser()._enforce()

    def policy_fixture(self, *, complete=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        vault = Path(temporary.name)
        policies = vault / "memory" / "policies"
        policies.mkdir(parents=True)
        loader = policies / "Policy Loader.md"
        loader.write_text(
            "## LEAD-IN\n\nRead the matching policy.\n\n"
            "## tools THAT carry it\n\n- write_note\n- edit_note\n"
            "## Discovery description\n\nFind active policy Problems.\n",
            encoding="utf-8",
        )
        (policies / "Fixture policy.md").write_text(
            "---\ntitle: Fixture policy\ntype: policy\nstatus: active\n---\n"
            "## Problem\n\nWhich fixture applies?\n"
            + ("\n## Conjecture\n\nThe named fixture applies.\n" if complete else ""),
            encoding="utf-8",
        )
        return vault, loader

    def require_shared_tools(self):
        tools = self.proxy._load_shared_tools()
        if tools is None:
            self.skipTest("The configured shared Perspirator toolkit is unavailable")
        return tools

    def test_shared_reader_does_not_fall_back_to_agent_copies(self):
        with patch.dict(os.environ, {"PERSPIRATOR_TOOLS": ""}):
            self.assertIsNone(self.proxy._load_shared_tools())

    def test_policy_loader_uses_canonical_case_insensitive_section_reading(self):
        shared = self.require_shared_tools()
        vault, loader = self.policy_fixture()
        with patch.object(self.proxy, "VAULT_PATH", vault), \
                patch.object(self.proxy, "POLICY_LOADER_NOTE", loader), \
                patch.object(self.proxy, "_policy_cache", None), \
                patch.object(self.proxy, "_load_shared_tools", return_value=shared):
            text, carriers = self.proxy.policy_payload()
            discovered = self.proxy.list_active_policies()
        self.assertIn("Read the matching policy.", text)
        self.assertNotIn("Fixture policy", text)
        self.assertEqual(discovered['status'], 'complete')
        self.assertEqual(discovered['policies'][0]['title'], 'Fixture policy')
        self.assertNotIn(self.proxy.POLICY_UNAVAILABLE, text)
        self.assertEqual(carriers, frozenset({"write_note", "edit_note"}))

    def test_missing_shared_reader_discloses_unavailable_policy(self):
        _, loader = self.policy_fixture()
        with patch.object(self.proxy, "POLICY_LOADER_NOTE", loader), \
                patch.object(self.proxy, "_policy_cache", None), \
                patch.object(self.proxy, "_load_shared_tools", return_value=None):
            self.assertEqual(self.proxy.policy_payload(),
                             (self.proxy.POLICY_UNAVAILABLE, None))
            healthy, detail = self.proxy.policy_status()
        self.assertFalse(healthy)
        self.assertIn("shared structural tools unavailable", detail)

    def test_malformed_active_policy_retains_carriers_and_disclosure(self):
        shared = self.require_shared_tools()
        vault, loader = self.policy_fixture(complete=False)
        with patch.object(self.proxy, "VAULT_PATH", vault), \
                patch.object(self.proxy, "POLICY_LOADER_NOTE", loader), \
                patch.object(self.proxy, "_policy_cache", None), \
                patch.object(self.proxy, "_load_shared_tools", return_value=shared):
            self.assertEqual(
                self.proxy.policy_payload(),
                (self.proxy.POLICY_UNAVAILABLE, frozenset({"write_note", "edit_note"})),
            )
            healthy, detail = self.proxy.policy_status()
        self.assertFalse(healthy)
        self.assertIn("no active policy surface", detail)

    def test_catalogue_is_fresh_and_not_repeated_in_tool_descriptions(self):
        shared = self.require_shared_tools()
        vault, loader = self.policy_fixture()
        tools = [SimpleNamespace(name=n, description='Original',
            model_copy=lambda update, n=n: SimpleNamespace(name=n, **update))
            for n in ['write_note', 'edit_note', 'read_note', 'list_active_policies']]
        async def next_call(_):
            return tools
        with patch.object(self.proxy, 'VAULT_PATH', vault), \
                patch.object(self.proxy, 'POLICY_LOADER_NOTE', loader), \
                patch.object(self.proxy, '_policy_cache', None), \
                patch.object(self.proxy, '_load_shared_tools', return_value=shared):
            listed = asyncio.run(self.proxy.CarryPolicyReminder().on_list_tools(None, next_call))
            self.assertTrue(all('Fixture policy' not in t.description for t in listed))
            self.assertEqual(listed[2].description, 'Original')
            policy = loader.parent / 'Fixture policy.md'
            policy.write_text(policy.read_text().replace('Which fixture applies?', 'What changed?'))
            self.assertEqual(self.proxy.list_active_policies()['policies'][0]['problem'], 'What changed?')
            policy.write_text('---\ntype: policy\nstatus: active\n---\nBroken')
            self.assertEqual(self.proxy.list_active_policies()['status'], 'unavailable')


if __name__ == "__main__":
    unittest.main()
