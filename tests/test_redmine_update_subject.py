from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from redmine_mcp import api
from redmine_mcp.client import RedmineClient
from redmine_mcp.config import RedmineSettings
from redmine_mcp.errors import RedmineMcpError


OLD_SUBJECT = "[WEBEMS/Maintenance/Schedule] Double-clicking Save creates two schedules"
NEW_SUBJECT = "[AI][WEB EMS/Maintenance/Schedule] Double-clicking Save creates two schedules"


class FakeSubjectClient:
    def __init__(self, subject: str = OLD_SUBJECT) -> None:
        self.issue = {
            "id": 88,
            "subject": subject,
            "status": {"id": 1, "name": "New"},
            "tracker": {"id": 1, "name": "Bug"},
            "priority": {"id": 4, "name": "L3"},
            "project": {"id": 738, "name": "EMS"},
            "assigned_to": None,
            "fixed_version": None,
            "category": {"id": 47365, "name": "b15"},
        }
        self.updates: list[tuple[str, str]] = []
        self.reads = 0
        self.fail_update = False
        self.ignore_update = False
        self.after_update_changes: dict = {}

    def issue_url(self, issue_id):
        return f"https://redmine.example.com/issues/{issue_id}"

    def get_issue(self, issue_id, *, include=None):
        self.reads += 1
        return json.loads(json.dumps(self.issue))

    def update_issue_subject(self, issue_id, subject):
        self.updates.append((str(issue_id), subject))
        if self.fail_update:
            raise RedmineMcpError("Redmine HTTP 422: invalid", code="HTTP_422")
        if not self.ignore_update:
            self.issue["subject"] = subject
        self.issue.update(self.after_update_changes)
        return {}


def settings() -> RedmineSettings:
    return RedmineSettings(
        url="https://redmine.example.com",
        api_key="redmine-secret",
        environment="sandbox",
        project_id="ems",
        template_file="",
    )


def args(**overrides):
    values = {
        "operation_id": "operation-subject-1",
        "environment": "sandbox",
        "issue_id": "88",
        "subject": NEW_SUBJECT,
    }
    values.update(overrides)
    return values


class RedmineUpdateSubjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeSubjectClient()
        self.tmpdir = TemporaryDirectory()
        self.patcher = patch("redmine_mcp.api._runtime", return_value=(settings(), self.client))
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
        result = api.redmine_update_subject(**args(**overrides))
        self.assertTrue(result["ok"], result)
        return result["result"]

    def write(self, digest: str, **overrides) -> dict:
        return api.redmine_update_subject(
            **args(**overrides), write=True, preview_digest=digest, audit_dir=self.tmpdir.name
        )

    def test_preview_shows_change_and_never_writes(self) -> None:
        preview = self.preview()

        self.assertEqual("preview", preview["mode"])
        self.assertEqual("update-subject", preview["action"])
        self.assertTrue(preview["planned_write"])
        self.assertEqual({"from": OLD_SUBJECT, "to": NEW_SUBJECT}, preview["subject_change"])
        self.assertEqual(["subject"], preview["write_fields"])
        self.assertRegex(preview["preview_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual([], self.client.updates)
        self.assertEqual([], self.audits())

    def test_preview_is_the_default_of_the_tool_call(self) -> None:
        result = api.call_tool("redmine_update_subject", args())

        self.assertEqual("preview", result["result"]["mode"])
        self.assertEqual([], self.client.updates)

    def test_digest_binds_issue_current_and_new_subject(self) -> None:
        base = self.preview()["preview_digest"]
        other_subject = self.preview(subject=NEW_SUBJECT + " v2")["preview_digest"]
        other_issue = self.preview(issue_id="89")["preview_digest"]
        self.client.issue["subject"] = "someone else's subject"
        other_current = self.preview()["preview_digest"]

        self.assertEqual(4, len({base, other_subject, other_issue, other_current}))

    def test_confirmed_write_sends_only_subject_reads_back_and_audits(self) -> None:
        digest = self.preview()["preview_digest"]
        result = self.write(digest)

        self.assertTrue(result["ok"], result)
        body = result["result"]
        self.assertEqual("updated", body["status"])
        self.assertEqual([("88", NEW_SUBJECT)], self.client.updates)
        self.assertTrue(body["verification"]["verified"])
        self.assertTrue(body["verification"]["other_fields_unchanged"])
        self.assertEqual({"from": OLD_SUBJECT, "to": NEW_SUBJECT}, body["subject_change"])
        audits = self.audits()
        self.assertEqual(1, len(audits))
        self.assertEqual("updated", audits[0]["status"])
        self.assertEqual("update-subject", audits[0]["action"])
        self.assertEqual(OLD_SUBJECT, audits[0]["previous_subject"])
        self.assertEqual(NEW_SUBJECT, audits[0]["new_subject"])
        self.assertEqual(digest, audits[0]["preview_digest"])
        self.assertEqual(body["audit_id"], Path(self.tmpdir.name, body["audit_id"]).name)
        self.assertNotIn("redmine-secret", json.dumps(audits))

    def test_wrong_or_missing_digest_is_rejected_without_write(self) -> None:
        self.preview()
        for digest in ("", "0" * 64):
            with self.subTest(digest=digest):
                result = self.write(digest)
                self.assertFalse(result["ok"])
                self.assertEqual("PREVIEW_DIGEST_MISMATCH", result["error"]["error"]["code"])
        self.assertEqual([], self.client.updates)

    def test_digest_for_different_new_subject_is_rejected(self) -> None:
        digest = self.preview()["preview_digest"]
        result = self.write(digest, subject=NEW_SUBJECT + " edited")

        self.assertFalse(result["ok"])
        self.assertEqual("PREVIEW_DIGEST_MISMATCH", result["error"]["error"]["code"])
        self.assertEqual([], self.client.updates)

    def test_subject_changed_by_someone_else_after_preview_is_not_overwritten(self) -> None:
        digest = self.preview()["preview_digest"]
        self.client.issue["subject"] = "RD renamed this issue"

        result = self.write(digest)

        self.assertFalse(result["ok"])
        self.assertEqual("PREVIEW_DIGEST_MISMATCH", result["error"]["error"]["code"])
        self.assertIn("RD renamed this issue", result["error"]["error"]["message"])
        self.assertEqual([], self.client.updates)
        self.assertEqual("RD renamed this issue", self.client.issue["subject"])
        self.assertEqual("failed", self.audits()[0]["status"])

    def test_expected_current_subject_mismatch_fails_in_preview_and_write(self) -> None:
        preview = api.redmine_update_subject(**args(expected_current_subject="stale subject"))
        self.assertFalse(preview["ok"])
        self.assertEqual("SUBJECT_CHANGED", preview["error"]["error"]["code"])

        digest = self.preview(expected_current_subject=OLD_SUBJECT)["preview_digest"]
        self.client.issue["subject"] = "RD renamed this issue"
        result = self.write(digest, expected_current_subject=OLD_SUBJECT)

        self.assertFalse(result["ok"])
        self.assertEqual("SUBJECT_CHANGED", result["error"]["error"]["code"])
        self.assertEqual([], self.client.updates)

    def test_readback_mismatch_fails_with_partial_result_and_audit(self) -> None:
        digest = self.preview()["preview_digest"]
        self.client.ignore_update = True

        result = self.write(digest)

        self.assertFalse(result["ok"])
        error = result["error"]
        self.assertEqual("VERIFICATION_FAILED", error["error"]["code"])
        self.assertEqual(OLD_SUBJECT, error["partial_result"]["verification"]["actual_subject"])
        audit = self.audits()[0]
        self.assertEqual("verification-failed", audit["status"])
        self.assertTrue(audit["subject_written"])

    def test_other_field_change_seen_in_readback_is_warned(self) -> None:
        digest = self.preview()["preview_digest"]
        self.client.after_update_changes = {"status": {"id": 3, "name": "Resolved"}}

        result = self.write(digest)

        self.assertTrue(result["ok"], result)
        verification = result["result"]["verification"]
        self.assertFalse(verification["other_fields_unchanged"])
        self.assertEqual(["status"], verification["changed_fields"])
        self.assertIn("status", result["result"]["warnings"][0])

    def test_put_failure_is_audited_as_failed(self) -> None:
        digest = self.preview()["preview_digest"]
        self.client.fail_update = True

        result = self.write(digest)

        self.assertFalse(result["ok"])
        self.assertEqual("HTTP_422", result["error"]["error"]["code"])
        audit = self.audits()[0]
        self.assertEqual("failed", audit["status"])
        self.assertFalse(audit["subject_written"])

    def test_retry_after_success_is_skipped_without_second_put(self) -> None:
        digest = self.preview()["preview_digest"]
        first = self.write(digest)
        second = self.write(digest)

        self.assertTrue(first["ok"])
        self.assertEqual("skipped-resume", second["result"]["status"])
        self.assertEqual(1, len(self.client.updates))

    def test_retry_after_timeout_that_reached_redmine_recovers(self) -> None:
        digest = self.preview()["preview_digest"]

        def applied_then_timeout(issue_id, subject):
            self.client.updates.append((str(issue_id), subject))
            self.client.issue["subject"] = subject
            raise RedmineMcpError("Redmine connection failed: timed out", code="CONNECTION_FAILED")

        with patch.object(self.client, "update_issue_subject", side_effect=applied_then_timeout):
            first = self.write(digest)
        second = self.write(digest)

        self.assertFalse(first["ok"])
        self.assertEqual("skipped-resume", second["result"]["status"])
        self.assertEqual(1, len(self.client.updates))
        audit = self.audits()[0]
        self.assertEqual("updated", audit["status"])
        self.assertTrue(audit["recovered"])

    def test_same_subject_is_no_change_and_write_is_blocked(self) -> None:
        preview = self.preview(subject=OLD_SUBJECT)
        self.assertEqual("no-change", preview["action"])
        self.assertFalse(preview["planned_write"])

        result = self.write(preview["preview_digest"], subject=OLD_SUBJECT)
        self.assertEqual("WRITE_BLOCKED", result["error"]["error"]["code"])
        self.assertEqual([], self.client.updates)

    def test_invalid_subjects_are_rejected_before_any_redmine_call(self) -> None:
        cases = {
            "blank": "   ",
            "newline": "[AI] first line\nsecond line",
            "carriage return": "[AI] title\r",
            "tab": "[AI]\ttitle",
            "too long": "x" * 256,
        }
        for name, subject in cases.items():
            with self.subTest(name):
                result = api.redmine_update_subject(**args(subject=subject))
                self.assertFalse(result["ok"])
                self.assertEqual("INVALID_ARGUMENT", result["error"]["error"]["code"])
        self.assertEqual(0, self.client.reads)
        self.assertTrue(api.redmine_update_subject(**args(subject="x" * 255))["ok"])

    def test_environment_mismatch_fails_before_reading_issue(self) -> None:
        result = api.redmine_update_subject(**args(environment="corp"))

        self.assertFalse(result["ok"])
        self.assertEqual(0, self.client.reads)


class RedmineClientSubjectTests(unittest.TestCase):
    def test_update_issue_subject_puts_subject_only(self) -> None:
        class RecordingClient(RedmineClient):
            def __init__(self):
                super().__init__("https://redmine.example.com", "redmine-secret")
                self.calls = []

            def request_json(self, method, path, payload=None, query=None):
                self.calls.append((method, path, payload, query))
                return {}

        client = RecordingClient()
        client.update_issue_subject("123", NEW_SUBJECT)

        self.assertEqual(
            [("PUT", "/issues/123.json", {"issue": {"subject": NEW_SUBJECT}}, None)],
            client.calls,
        )


if __name__ == "__main__":
    unittest.main()
