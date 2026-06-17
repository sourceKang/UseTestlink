import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core.profiles import (
    apply_create_profile,
    delete_profile,
    list_profiles,
    profile_from_suite_search_row,
    profile_from_values,
    profiles_path,
    read_profiles,
    save_profile,
)


class ProfileTests(unittest.TestCase):
    def test_saves_lists_and_deletes_profile(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profiles.json"
            profile = profile_from_values(
                project="Gateway",
                suite_id="695420",
                suite_name="VPN",
                testprojectid="339165",
            )

            save_profile(path, "gateway-vpn", profile)
            rows = list_profiles(path)
            removed = delete_profile(path, "gateway-vpn")

            self.assertEqual(rows[0]["name"], "gateway-vpn")
            self.assertEqual(rows[0]["project"], "Gateway")
            self.assertEqual(rows[0]["suite_id"], "695420")
            self.assertEqual(removed["suite_name"], "VPN")
            self.assertEqual(read_profiles(path)["profiles"], {})

    def test_profile_from_suite_search_row(self):
        profile = profile_from_suite_search_row(
            {
                "project": "Gateway",
                "testprojectid": "339165",
                "suite_id": "695420",
                "suite_name": "VPN",
                "suite_path": "VPN",
            }
        )

        self.assertEqual(profile["project"], "Gateway")
        self.assertEqual(profile["suite_id"], "695420")
        self.assertEqual(profile["suite_path"], "VPN")

    def test_apply_create_profile_fills_missing_target(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "profiles.json"
            save_profile(
                path,
                "gateway-vpn",
                profile_from_values(project="Gateway", suite_id="695420", suite_name="VPN"),
            )
            args = argparse.Namespace(
                profile="gateway-vpn",
                profiles=str(path),
                project=None,
                suite_id=None,
                suite_name=None,
            )

            profile = apply_create_profile(args)

            self.assertEqual(profile["project"], "Gateway")
            self.assertEqual(args.project, "Gateway")
            self.assertEqual(args.suite_id, "695420")
            self.assertIsNone(args.suite_name)

    def test_profiles_path_default(self):
        self.assertEqual(profiles_path(), Path("local/testlink_profiles.json"))


if __name__ == "__main__":
    unittest.main()
