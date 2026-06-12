import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent import parse_report


class ReportParserTests(unittest.TestCase):
    def test_parses_nested_brackets_and_skip(self):
        content = """Report generated on: 2026-06-12_13-26-09
EMS Version: 03.00.11 (AAVV.221) b5
Node Name: Taiwan_NeoX-03_169.58
Node IP: 192.168.169.58
Test Results:
-------------
[EMS1-7137][test_neox_profile_error_readwrite[IGMPGroupPrivilegeProfile]] Result Skip (0s)
[EMS1-6682][test_get_port_by_devicename] Result Fail (0s)
[EMS1-6640][test_get_sessionid] Result Pass (0.1s)
"""
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report.txt"
            report.write_text(content, encoding="utf-8")
            header, results = parse_report(report)

        self.assertEqual(header["Report generated on"], "2026-06-12_13-26-09")
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].test_name, "test_neox_profile_error_readwrite[IGMPGroupPrivilegeProfile]")
        self.assertIsNone(results[0].status)
        self.assertEqual(results[1].status, "f")
        self.assertEqual(results[2].status, "p")


if __name__ == "__main__":
    unittest.main()
