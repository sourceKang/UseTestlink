import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core.errors import TestLinkError
from testlink_agent_core.reports import choose_latest_open_build, junit_external_id, parse_report


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


    def test_parses_junit_xml_and_recovers_external_ids(self):
        content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4" timestamp="2026-08-31T15:12:14.922374+08:00">
<testcase classname="tests.test_port_inventory_ui" name="test_ems1_3581_all_port_info" time="0.429"><failure message="boom">detail</failure></testcase>
<testcase classname="tests.test_port_inventory_ui" name="test_ems1_3535_all_port_filter" time="5.050"/>
<testcase classname="tests.test_ont_inventory_ui" name="test_ems1_4514_all_ont_page" time="6.166"><skipped message="needs device"/></testcase>
<testcase classname="tests.test_helpers" name="test_local_helper_only" time="0.010"/>
</testsuite></testsuites>
"""
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "results.xml"
            report.write_text(content, encoding="utf-8")
            header, results = parse_report(report)

        self.assertEqual(header["_schema_version"], "junit-xml-v1")
        self.assertEqual(header["Summary"], "2 Pass / 1 Fail / 1 Skipped")
        self.assertEqual(header["Unmapped Tests"], "1")
        self.assertEqual([item.external_id for item in results], ["EMS1-3581", "EMS1-3535", "EMS1-4514"])
        self.assertEqual(results[0].status, "f")
        self.assertEqual(results[0].duration_seconds, 0.429)
        self.assertEqual(results[1].status, "p")
        self.assertEqual(results[2].raw_status, "Skipped")
        self.assertIsNone(results[2].status)

    def test_junit_external_id_accepts_both_spellings(self):
        self.assertEqual("EMS1-3581", junit_external_id("test_ems1_3581_all_port_info", ""))
        self.assertEqual("EMS1-3581", junit_external_id("test_all_port_info[EMS1-3581]", ""))
        self.assertEqual("GW-42", junit_external_id("", "tests.gw.test_gw_42_login"))
        self.assertIsNone(junit_external_id("test_get_port_by_devicename", "tests.helpers"))

    def test_rejects_junit_without_any_external_id(self):
        content = '<testsuite name="pytest"><testcase classname="t" name="test_plain" time="1"/></testsuite>'
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "results.xml"
            report.write_text(content, encoding="utf-8")

            with self.assertRaisesRegex(TestLinkError, "no testcase carried a TestLink external ID"):
                parse_report(report)

    def test_rejects_malformed_junit_xml(self):
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "results.xml"
            report.write_text('<testsuite name="pytest"><testcase name="test_ems1_1_x"', encoding="utf-8")

            with self.assertRaisesRegex(TestLinkError, "not well formed"):
                parse_report(report)

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
