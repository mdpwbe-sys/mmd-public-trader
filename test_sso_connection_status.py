"""SSO presence exposed to the UI must match a usable local access token."""
import unittest
from unittest import mock

import mmd_sso


class SsoConnectionStatusTests(unittest.TestCase):
    def test_connected_chars_omits_profiles_without_an_access_token(self):
        cache = {
            "characters": {
                "1": {"name": "Connected", "access_token": "test-token", "scopes": mmd_sso.REQUIRED_SCOPES},
                "2": {"name": "Disconnected", "access_token": "", "scopes": mmd_sso.REQUIRED_SCOPES},
            }
        }
        with mock.patch.object(mmd_sso, "_chars", return_value=cache["characters"]):
            self.assertEqual(
                mmd_sso.connected_chars(),
                [{"id": 1, "name": "Connected", "scopes_ok": True}],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
