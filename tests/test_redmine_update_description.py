from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from redmine_mcp import api
from redmine_mcp.config import RedmineSettings
from redmine_mcp.errors import RedmineMcpError


# Redmine returns stored descriptions with CRLF line endings.
OLD_DESCRIPTION = "### Description\r\n\r\nOld text.\r\n\r\n### Steps to reproduce\r\n\r\n1. Open page."
NEW_DESCRIPTION = "### Description\n\nNew text.\n\n### Steps to reproduce\n\n1. Open page."


class FakeDescriptionClient:
    def __init__(self) -> None:
        self.issue = {
            "id": 88,
            "subject": "[AI][WEB EMS/Maintenance] Example",
            "description": OLD_DESCRIPTION,
            "status": {"id": 4, "name": "Assigned"},
            "tracker": {"id": 1, "name": "Bug"},
            "priority": {"id": 5, "name": "L2"},
            "project": {"id": 738, "name": "EMS"},
            "assigned_to": {"id": 9, "name": "RD"},
            "fixed_version": {"id": 77, "name": "C0"},
            "category": {"id": 47365, "name": "b15"},
        }
        self.updates: list[tuple[str, str, str]] = []
        self.reads = 0
        self.ignore_update = False
        self.after_update_changes: dict = {}

    def issue_url(self, issue_id):
        return f"https://redmine.example.com/issues/{issue_id}"

    def get_issue(self, issue_id, *, include=None):
        self.reads += 1
        return json.loads(json.dumps(self.issue))

    def update_issue_field(self, issue_id, field, value):
        self.updates.append((str(issue_id), field, value))
        if not self.ignore_update:
            # Simulate Redmine storing CRLF line endings.
            self.issue[field] = value.replace("\n", "\r\n")
        self.issue.update(self.after_update_changes)
        return {}


def settings(environment: str = "sandbox", template_file: str = "") -> RedmineSettings:
    return RedmineSettings(
        url="https://redmine.example.com",
        api_key="redmine-secret",
        environment=environment,
        project_id="ems",
        template_file=template_file,
    )


def write_markdown_template(directory: str) -> Path:
    path = Path(directory) / "redmine-template.json"
    path.write_text(
        json.dumps(
            {
                "project_id": "ems",
                "tracker_id": 1,
                "priority_id": 2,
                "text_format": {"engine": "markdown", "validation": "strict", "policy_version": 1},
                "custom_fields": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def args(**overrides):
    values = {
        "operation_id": "operation-description-1",
        "environment": "sandbox",
        "issue_id": "88",
        "description": NEW_DESCRIPTION,
    }
    values.update(overrides)
    return values


class RedmineUpdateDescriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeDescriptionClient()
        self.tmpdir = TemporaryDirectory()
        self.settings = settings()
        self.patcher = patch("redmine_mcp.api._runtime", side_effect=lambda *_: (self.settings, self.client))
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmpdir.cleanup()

    def audits(self) -> list[dict]:
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(Path(self.tmpdir.name).glob("*.json"))
        ]

    def preview(self, **overrides) -> dict:
        result = api.redmine_update_description(**args(**overrides))
        self.assertTrue(result["ok"], result)
        return result["result"]

    def write(self, digest: str, **overrides) -> dict:
        return api.redmine_update_description(
            **args(**overrides), write=True, preview_digest=digest, audit_dir=self.tmpdir.name
        )

    def test_preview_shows_full_text_and_diff_without_writing(self) -> None:
        preview = self.preview()

        self.assertEqual("update-description", preview["action"])
        self.assertTrue(preview["planned_write"])
        change = preview["description_change"]
        self.assertEqual(OLD_DESCRIPTION, change["from"])
        self.assertEqual(NEW_DESCRIPTION, change["to"])
        self.assertIn("-Old text.", change["diff"])
        self.assertIn("+New text.", change["diff"])
        self.assertEqual(["description"], preview["write_fields"])
        self.assertEqual([], self.client.updates)
        self.assertEqual([], self.audits())

    def test_preview_is_the_default_of_the_tool_call(self) -> None:
        result = api.call_tool("redmine_update_description", args())

        self.assertEqual("preview", result["result"]["mode"])
        self.assertEqual([], self.client.updates)

    def test_confirmed_write_sends_only_description_reads_back_and_audits(self) -> None:
        digest = self.preview()["preview_digest"]
        result = self.write(digest)

        self.assertTrue(result["ok"], result)
        body = result["result"]
        self.assertEqual("updated", body["status"])
        self.assertEqual([("88", "description", NEW_DESCRIPTION)], self.client.updates)
        # CRLF readback still verifies against the LF text that was sent.
        self.assertTrue(body["verification"]["verified"])
        self.assertTrue(body["verification"]["other_fields_unchanged"])
        audit = self.audits()[0]
        self.assertEqual("update-description", audit["action"])
        self.assertEqual("updated", audit["status"])
        self.assertEqual(OLD_DESCRIPTION, audit["previous_description"])
        self.assertEqual(NEW_DESCRIPTION, audit["new_description"])
        self.assertNotIn("redmine-secret", json.dumps(audit))

    def test_description_changed_by_someone_else_after_preview_is_not_overwritten(self) -> None:
        digest = self.preview()["preview_digest"]
        self.client.issue["description"] = OLD_DESCRIPTION + "\r\n\r\nRD note added."

        result = self.write(digest)

        self.assertFalse(result["ok"])
        self.assertEqual("PREVIEW_DIGEST_MISMATCH", result["error"]["error"]["code"])
        self.assertEqual([], self.client.updates)
        self.assertIn("RD note added.", self.client.issue["description"])

    def test_digest_for_a_different_new_description_is_rejected(self) -> None:
        digest = self.preview()["preview_digest"]
        result = self.write(digest, description=NEW_DESCRIPTION + "\n\nExtra line.")

        self.assertEqual("PREVIEW_DIGEST_MISMATCH", result["error"]["error"]["code"])
        self.assertEqual([], self.client.updates)

    def test_readback_mismatch_fails_with_partial_result(self) -> None:
        digest = self.preview()["preview_digest"]
        self.client.ignore_update = True

        result = self.write(digest)

        self.assertEqual("VERIFICATION_FAILED", result["error"]["error"]["code"])
        self.assertFalse(result["error"]["partial_result"]["verification"]["verified"])
        self.assertEqual("verification-failed", self.audits()[0]["status"])

    def test_subject_or_reference_change_in_readback_is_warned(self) -> None:
        digest = self.preview()["preview_digest"]
        self.client.after_update_changes = {"subject": "renamed meanwhile", "fixed_version": None}

        result = self.write(digest)

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            ["fixed_version", "subject"], sorted(result["result"]["verification"]["changed_fields"])
        )

    def test_retry_after_success_is_skipped_without_second_put(self) -> None:
        digest = self.preview()["preview_digest"]
        self.write(digest)
        second = self.write(digest)

        self.assertEqual("skipped-resume", second["result"]["status"])
        self.assertEqual(1, len(self.client.updates))

    def test_timeout_after_redmine_applied_the_put_recovers_on_retry(self) -> None:
        digest = self.preview()["preview_digest"]
        original = self.client.update_issue_field

        def applied_then_timeout(issue_id, field, value):
            original(issue_id, field, value)
            raise RedmineMcpError("Redmine connection failed: timed out", code="CONNECTION_FAILED")

        with patch.object(self.client, "update_issue_field", side_effect=applied_then_timeout):
            first = self.write(digest)
        second = self.write(digest)

        self.assertFalse(first["ok"])
        self.assertEqual("skipped-resume", second["result"]["status"])
        self.assertEqual(1, len(self.client.updates))
        self.assertTrue(self.audits()[0]["recovered"])

    def test_same_description_ignoring_line_endings_is_no_change(self) -> None:
        preview = self.preview(description=OLD_DESCRIPTION.replace("\r\n", "\n"))

        self.assertEqual("no-change", preview["action"])
        result = self.write(preview["preview_digest"], description=OLD_DESCRIPTION.replace("\r\n", "\n"))
        self.assertEqual("WRITE_BLOCKED", result["error"]["error"]["code"])
        self.assertEqual([], self.client.updates)

    def test_corporate_markdown_template_blocks_textile_heading(self) -> None:
        template = write_markdown_template(self.tmpdir.name)
        self.settings = settings("corp", str(template))

        preview = self.preview(environment="corp", description="h3. Description\n\nText")
        self.assertEqual("blocked", preview["action"])
        self.assertEqual("TEXTILE_HEADING_IN_MARKDOWN", preview["format_validation"]["errors"][0]["code"])

        result = self.write(preview["preview_digest"], environment="corp", description="h3. Description\n\nText")
        self.assertEqual("WRITE_BLOCKED", result["error"]["error"]["code"])
        self.assertEqual([], self.client.updates)

    def test_corporate_markdown_description_is_allowed(self) -> None:
        template = write_markdown_template(self.tmpdir.name)
        self.settings = settings("corp", str(template))

        preview = self.preview(environment="corp")
        result = self.write(preview["preview_digest"], environment="corp")

        self.assertTrue(result["ok"], result)
        self.assertEqual(1, len(self.client.updates))

    def test_invalid_descriptions_are_rejected_before_any_redmine_call(self) -> None:
        for name, description in {"blank": " \n\t\n ", "nul": "text\x00more", "escape": "a\x1bb"}.items():
            with self.subTest(name):
                result = api.redmine_update_description(**args(description=description))
                self.assertEqual("INVALID_ARGUMENT", result["error"]["error"]["code"])
        self.assertEqual(0, self.client.reads)

    def test_description_tool_does_not_accept_other_fields(self) -> None:
        result = api.call_tool("redmine_update_description", {**args(), "status_id": "5"})

        self.assertFalse(result["ok"])
        self.assertEqual("INVALID_ARGUMENT", result["error"]["error"]["code"])
        self.assertEqual(0, self.client.reads)


if __name__ == "__main__":
    unittest.main()
