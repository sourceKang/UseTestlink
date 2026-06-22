import unittest

from testlink_agent_core.catalog import find_suites_in_catalog
from testlink_agent_core.clients import xmlrpc_items_to_list
from testlink_agent_core.suites import resolve_suite_by_name, suite_search_row


class SuiteCatalogTests(unittest.TestCase):
    def test_resolves_suite_by_name_or_path(self):
        suites = [
            {"id": "10", "name": "API", "path": "Root/API"},
            {"id": "11", "name": "UI", "path": "Root/UI"},
            {"id": "12", "name": "API", "path": "Root/Nested/API"},
        ]

        by_name = resolve_suite_by_name(suites, "UI")
        by_path = resolve_suite_by_name(suites, "Root/API")

        self.assertEqual(by_name["id"], "11")
        self.assertEqual(by_path["id"], "10")

    def test_resolves_exact_path_before_duplicate_names(self):
        suites = [
            {"id": "10", "name": "VPN", "path": "VPN"},
            {"id": "11", "name": "VPN", "path": "VPN/Nested/VPN"},
        ]

        resolved = resolve_suite_by_name(suites, "VPN")

        self.assertEqual(resolved["id"], "10")

    def test_normalizes_xmlrpc_dict_maps_to_list(self):
        rows = xmlrpc_items_to_list(
            {
                "10": {"id": "10", "name": "API"},
                "11": {"id": "11", "name": "UI"},
            }
        )

        self.assertEqual([row["id"] for row in rows], ["10", "11"])

    def test_suite_search_row_includes_create_args(self):
        row = suite_search_row(
            {"id": "20", "name": "Gateway"},
            {"id": "99", "name": "VPN", "path": "VPN"},
        )

        self.assertEqual(row["project"], "Gateway")
        self.assertEqual(row["suite_id"], "99")
        self.assertEqual(row["create_args"], ["--project", "Gateway", "--suite-id", "99"])
        self.assertIn('--project "Gateway"', row["create_example"])
        self.assertIn("--suite-id 99", row["create_example"])

    def test_finds_suites_in_catalog(self):
        catalog = {
            "projects": [
                {
                    "id": "20",
                    "name": "Gateway",
                    "active": "1",
                    "suites": [
                        {"id": "99", "name": "VPN", "path": "VPN"},
                        {"id": "100", "name": "LAN", "path": "LAN"},
                    ],
                },
                {
                    "id": "21",
                    "name": "Archive",
                    "active": "0",
                    "suites": [{"id": "101", "name": "VPN", "path": "VPN"}],
                },
            ]
        }

        matches, scanned = find_suites_in_catalog(catalog, "Gateway", "VPN", active_only=True)

        self.assertEqual(len(scanned), 1)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["project"], "Gateway")
        self.assertEqual(matches[0]["suite_id"], "99")


if __name__ == "__main__":
    unittest.main()
