import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core import api


class ApiTests(unittest.TestCase):
    def test_profile_roundtrip_returns_structured_payloads(self):
        with TemporaryDirectory() as tmpdir:
            profiles = str(Path(tmpdir) / "profiles.json")

            saved = api.save_profile(
                "gateway-vpn",
                project="Gateway",
                suite_id="695420",
                suite_name="VPN",
                profiles=profiles,
            )
            listed = api.list_profiles(profiles=profiles)
            deleted = api.delete_profile("gateway-vpn", profiles=profiles)

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["result"]["profile"], "gateway-vpn")
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["result"]["profile_count"], 1)
        self.assertEqual(listed["result"]["profiles"][0]["suite_id"], "695420")
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["result"]["removed"]["project"], "Gateway")

    def test_secret_arguments_are_redacted_from_errors(self):
        result = api.call_tool("missing-tool", {"devkey": "secret"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "UnknownTool")


if __name__ == "__main__":
    unittest.main()
