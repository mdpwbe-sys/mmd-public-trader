"""Keep the Settings JS calls aligned with the pywebview Api signatures."""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parent


class TradeSettingsContractTests(unittest.TestCase):
    @staticmethod
    def _api_class():
        # Unit tests run without pywebview; Api itself is safe to import when
        # its GUI dependency is represented by this inert module.
        sys.modules.setdefault("webview", types.SimpleNamespace())
        return importlib.import_module("mmd_gui").Api

    def test_global_trade_settings_are_called_without_a_context_argument(self):
        js = (ROOT / "gui" / "trading-ui.js").read_text(encoding="utf-8")
        self.assertIn("call('get_trade_settings')", js)
        self.assertNotIn("call('get_trade_settings',", js)
        self.assertIn("call('save_trade_settings', payload)", js)
        api = self._api_class()
        self.assertEqual(list(inspect.signature(api.get_trade_settings).parameters), ["self"])
        self.assertEqual(list(inspect.signature(api.save_trade_settings).parameters), ["self", "payload"])

    def test_neighboring_settings_methods_keep_their_explicit_contracts(self):
        page = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("api.get_broker_config()", page)
        self.assertIn("api.save_broker_config(JSON.stringify(cfg))", page)
        self.assertIn("a.fetch_esi_config()", page)
        api = self._api_class()
        self.assertEqual(list(inspect.signature(api.get_broker_config).parameters), ["self"])
        self.assertEqual(list(inspect.signature(api.save_broker_config).parameters), ["self", "cfg"])
        self.assertEqual(list(inspect.signature(api.fetch_esi_config).parameters), ["self"])

    def test_new_install_without_sso_returns_empty_settings_without_an_exception(self):
        import portfolio_service

        no_sso_discovery = {"characters": [], "divisions": {}, "containers": {}, "errors": []}
        with patch.object(portfolio_service.migrations, "migrate"), \
             patch.object(portfolio_service.sso, "connected_chars", return_value=[]), \
             patch.object(portfolio_service.esi, "discover_sources", return_value=no_sso_discovery), \
             patch.object(portfolio_service.repo, "get_settings", return_value={}):
            result = portfolio_service.get_settings(force=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["divisions"], [])
        self.assertEqual(result["containers"], [])

    def test_api_delegates_global_settings_without_a_character_context(self):
        expected = {"ok": True, "divisions": [], "containers": [], "errors": []}
        fake_service = types.SimpleNamespace(get_settings=lambda *, force: expected)
        with patch.dict(sys.modules, {"portfolio_service": fake_service}):
            self.assertIs(self._api_class()().get_trade_settings(), expected)


if __name__ == "__main__":
    unittest.main()
