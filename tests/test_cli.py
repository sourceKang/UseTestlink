import unittest

from testlink_agent_core.cli import build_parser


class CliTests(unittest.TestCase):
    def test_upload_report_defaults_to_notes_only_bug_linking(self):
        parser = build_parser()

        args = parser.parse_args(
            [
                "upload-report",
                "--project",
                "EMS",
                "--plan",
                "Regression",
                "--platform",
                "NetAtlas EMS",
                "--report",
                "report.txt",
            ]
        )

        self.assertEqual(args.testlink_bug_link, "notes")

    def test_find_suites_parser_defaults(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "find-suites",
                "--project-contains",
                "Gateway",
                "--suite-contains",
                "VPN",
            ]
        )

        self.assertEqual(args.project_contains, "Gateway")
        self.assertEqual(args.suite_contains, "VPN")
        self.assertTrue(args.active_only)
        self.assertTrue(args.recursive)
        self.assertEqual(args.max_projects, 20)
        self.assertEqual(args.catalog, "local/testlink_catalog.json")
        self.assertFalse(args.refresh)
        self.assertFalse(args.offline)

    def test_refresh_catalog_parser_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["refresh-catalog"])

        self.assertEqual(args.out, "local/testlink_catalog.json")
        self.assertTrue(args.active_only)
        self.assertTrue(args.recursive)
        self.assertEqual(args.max_projects, 20)
        self.assertFalse(args.force)

    def test_create_testcase_accepts_profile_without_project_or_suite(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "create-testcase",
                "--profile",
                "gateway-vpn",
                "--name",
                "can_connect_vpn",
                "--summary",
                "Verify VPN connection.",
                "--step",
                "Connect VPN => VPN is connected",
            ]
        )

        self.assertEqual(args.profile, "gateway-vpn")
        self.assertIsNone(args.project)
        self.assertIsNone(args.suite_id)
        self.assertEqual(args.profiles, "local/testlink_profiles.json")

    def test_save_profile_parser_defaults(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "save-profile",
                "--name",
                "gateway-vpn",
                "--project",
                "Gateway",
                "--suite-id",
                "695420",
            ]
        )

        self.assertEqual(args.name, "gateway-vpn")
        self.assertEqual(args.project, "Gateway")
        self.assertEqual(args.suite_id, "695420")
        self.assertEqual(args.profiles, "local/testlink_profiles.json")
        self.assertTrue(args.active_only)
        self.assertTrue(args.recursive)
        self.assertFalse(args.refresh)
        self.assertFalse(args.offline)

    def test_update_testcase_accepts_profile_and_external_id(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "update-testcase",
                "--profile",
                "gateway-vpn",
                "--testcase-external-id",
                "GW-123",
                "--summary",
                "Updated summary.",
            ]
        )

        self.assertEqual(args.profile, "gateway-vpn")
        self.assertEqual(args.testcase_external_id, "GW-123")
        self.assertEqual(args.summary, "Updated summary.")
        self.assertIsNone(args.execution_type)
        self.assertFalse(args.write)


if __name__ == "__main__":
    unittest.main()
