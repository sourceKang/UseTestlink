import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent import parse_report


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
[PRJ-6640][test_get_sessionid] Result Pass (0.1s)
"""
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report.txt"
            report.write_text(content, encoding="utf-8")
            header, results = parse_report(report)

        self.assertEqual(header["Report generated on"], "2026-06-12_13-26-09")
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].test_name, "test_profile_error_readwrite[ExampleProfile]")
        self.assertIsNone(results[0].status)
        self.assertEqual(results[1].status, "f")
        self.assertEqual(results[2].status, "p")


if __name__ == "__main__":
    unittest.main()
