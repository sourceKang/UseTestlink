from __future__ import annotations

import unittest

from redmine_mcp.errors import RedmineMcpError
from redmine_mcp.text_format import normalize_text_format_contract, validate_redmine_text


STRICT_MARKDOWN = {
    "engine": "markdown",
    "validation": "strict",
    "policy_version": 1,
}


class RedmineTextFormatTests(unittest.TestCase):
    def validate(self, text: str, *, contract=STRICT_MARKDOWN, environment: str = "corp"):
        return validate_redmine_text(
            text,
            contract,
            environment=environment,
            field="description",
        )

    def test_valid_markdown_passes(self) -> None:
        result = self.validate("## Environment\n\n- EMS: 3.00\n- Result: failed")

        self.assertTrue(result["valid"])
        self.assertEqual([], result["errors"])

    def test_textile_heading_is_blocked(self) -> None:
        result = self.validate("h3. Environment")

        self.assertFalse(result["valid"])
        self.assertEqual("TEXTILE_HEADING_IN_MARKDOWN", result["errors"][0]["code"])
        self.assertEqual(1, result["errors"][0]["line"])

    def test_textile_table_header_is_blocked(self) -> None:
        result = self.validate("|_. Name |_. Value |\n| EMS | 3.00 |")

        self.assertFalse(result["valid"])
        self.assertEqual("TEXTILE_TABLE_HEADER_IN_MARKDOWN", result["errors"][0]["code"])

    def test_consecutive_hash_lines_are_blocked(self) -> None:
        result = self.validate("# Step one\n# Step two\n# Step three")

        self.assertFalse(result["valid"])
        self.assertEqual("TEXTILE_NUMBERED_LIST_IN_MARKDOWN", result["errors"][0]["code"])
        self.assertEqual(1, result["errors"][0]["line"])

    def test_single_hash_heading_only_warns(self) -> None:
        result = self.validate("# Environment\n\nEvidence")

        self.assertTrue(result["valid"])
        self.assertEqual("AMBIGUOUS_HASH_LINE_IN_MARKDOWN", result["warnings"][0]["code"])

    def test_fenced_code_is_not_scanned(self) -> None:
        result = self.validate("```text\nh3. Environment\n|_. Name |\n# Step one\n# Step two\n```")

        self.assertTrue(result["valid"])
        self.assertEqual([], result["errors"])
        self.assertEqual([], result["warnings"])

    def test_indented_code_is_not_scanned(self) -> None:
        result = self.validate("Log:\n\n    h3. Environment\n    |_. Name |")

        self.assertTrue(result["valid"])
        self.assertEqual([], result["errors"])

    def test_preformatted_html_is_not_scanned(self) -> None:
        result = self.validate("<pre>\nh3. Environment\n|_. Name |\n</pre>")

        self.assertTrue(result["valid"])
        self.assertEqual([], result["errors"])

    def test_warn_mode_reports_but_does_not_block_sandbox(self) -> None:
        result = self.validate(
            "h3. Environment",
            contract={"engine": "markdown", "validation": "warn", "policy_version": 1},
            environment="sandbox",
        )

        self.assertTrue(result["valid"])
        self.assertEqual([], result["errors"])
        self.assertEqual("TEXTILE_HEADING_IN_MARKDOWN", result["warnings"][0]["code"])

    def test_corporate_profile_requires_strict_mode(self) -> None:
        result = self.validate(
            "Evidence",
            contract={"engine": "markdown", "validation": "warn", "policy_version": 1},
        )

        self.assertFalse(result["valid"])
        self.assertEqual("STRICT_TEXT_FORMAT_REQUIRED", result["errors"][0]["code"])

    def test_missing_contract_blocks_corporate_write(self) -> None:
        result = self.validate("Evidence", contract=None)

        self.assertFalse(result["valid"])
        self.assertEqual("TEXT_FORMAT_NOT_CONFIGURED", result["errors"][0]["code"])

    def test_missing_contract_only_warns_in_sandbox(self) -> None:
        result = self.validate("Evidence", contract=None, environment="sandbox")

        self.assertTrue(result["valid"])
        self.assertEqual("TEXT_FORMAT_NOT_CONFIGURED", result["warnings"][0]["code"])

    def test_template_contract_rejects_unknown_field_and_policy_version(self) -> None:
        with self.assertRaisesRegex(RedmineMcpError, "unsupported fields"):
            normalize_text_format_contract({**STRICT_MARKDOWN, "caller_override": True})
        with self.assertRaisesRegex(RedmineMcpError, "policy_version must be 1"):
            normalize_text_format_contract({**STRICT_MARKDOWN, "policy_version": 2})


if __name__ == "__main__":
    unittest.main()
