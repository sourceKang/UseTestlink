import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core.errors import TestLinkError
from testlink_agent_core.reports import choose_latest_open_build, parse_report


class ReportParserTests(unittest.TestCase):
    def test_parses_nested_brackets_and_skip(self):
        content = """Report generated on: 2026-06-12_13-26-09
EMS Version: 1.2.3 build 5
Node Name: Example_Node
Node IP: 192.0.2.10
Test Results:
-------------
[PRJ-7137][test_profile_error_readwrite[ExampleProfile]] Result Skip (0s)
[PRJ-6682][test_get_port_by_devicename] Result Fail (0s)
[PRJ-6683][test_error_response] Result Error (1.5s)
[PRJ-6640][test_get_sessionid] Result Pass (0.1s)
"""
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report.txt"
            report.write_text(content, encoding="utf-8")
            header, results = parse_report(report)

        self.assertEqual(header["Report generated on"], "2026-06-12_13-26-09")
        self.assertEqual(header["_schema_version"], "legacy-web-ems-report-v1")
        self.assertEqual(len(results), 4)
        self.assertEqual(results[0].test_name, "test_profile_error_readwrite[ExampleProfile]")
        self.assertIsNone(results[0].status)
        self.assertEqual(results[1].status, "f")
        self.assertEqual(results[2].status, "f")
        self.assertEqual(results[2].raw_status, "Error")
        self.assertEqual(results[2].duration_seconds, 1.5)
        self.assertEqual(results[3].status, "p")

    def test_selects_latest_open_build(self):
        selected = choose_latest_open_build(
            [
                {"id": "1", "name": "old", "active": "1", "is_open": "1", "creation_ts": "2026-01-01 00:00:00"},
                {"id": "2", "name": "closed", "active": "1", "is_open": "0", "creation_ts": "2026-03-01 00:00:00"},
                {"id": "3", "name": "latest", "active": "1", "is_open": "1", "creation_ts": "2026-02-01 00:00:00"},
            ]
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected["id"], "3")

    def test_rejects_report_without_test_results_marker(self):
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report.txt"
            report.write_text("[PRJ-1][test_login] Result Pass (1s)\n", encoding="utf-8")

            with self.assertRaisesRegex(TestLinkError, "missing 'Test Results:'"):
                parse_report(report)

    def test_rejects_report_without_result_rows(self):
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report.txt"
            report.write_text("Report generated on: 2026-06-12\nTest Results:\n", encoding="utf-8")

            with self.assertRaisesRegex(TestLinkError, "no TestLink result rows"):
                parse_report(report)

    def test_rejects_non_utf8_report(self):
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report.txt"
            report.write_bytes(b"Test Results:\n\xff\xfe\xfa")

            with self.assertRaisesRegex(TestLinkError, "UTF-8"):
                parse_report(report)


if __name__ == "__main__":
    unittest.main()
