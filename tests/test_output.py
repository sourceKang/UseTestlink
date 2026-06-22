import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core.output import write_xlsx_output


class OutputTests(unittest.TestCase):
    def test_writes_xlsx_output(self):
        testcases = [
            {
                "external_id": "PRJ-100",
                "testcase_id": "100",
                "version": "2",
                "name": "can_login",
                "execution_order": "1",
                "platform_id": "49",
            }
        ]
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "testcases.xlsx"
            write_xlsx_output(testcases, str(output), force=False)

            self.assertTrue(output.exists())
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

        self.assertIn("[Content_Types].xml", names)
        self.assertIn("xl/workbook.xml", names)
        self.assertIn("xl/worksheets/sheet1.xml", names)
        self.assertIn("PRJ-100", sheet_xml)
        self.assertIn("can_login", sheet_xml)


if __name__ == "__main__":
    unittest.main()
