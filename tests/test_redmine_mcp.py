from __future__ import annotations

import json
import os
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from redmine_mcp import api
from redmine_mcp.audit import write_operation_audit
from redmine_mcp.client import RedmineClient
from redmine_mcp.config import RedmineSettings, load_redmine_settings
from redmine_mcp.errors import RedmineMcpError
from redmine_mcp.models import RedmineIssue


class FakeRedmineClient:
    def __init__(self) -> None:
        self.open_issue: RedmineIssue | None = None
        self.closed_issue: RedmineIssue | None = None
        self.created_payloads: list[dict] = []
        self.comments: list[tuple[str, str]] = []
        self.uploaded_files: list[tuple[str, bytes]] = []
        self.journals: list[dict] = []
        self.fail_create = False
        self.fail_comment = False
        self.fail_upload = False
        self.last_issue_payload: dict | None = None
        self.issue_readback_override: dict | None = None

    def health(self):
        return {"user": {"id": 7, "login": "qa-user"}}

    def find_issue_by_marker(self, *, project_id, marker, status_id, tracker_id=None):
        if status_id == "open":
            return self.open_issue
        if status_id == "closed":
            return self.closed_issue
        return None

    def find_issues(self, *, project_id, status_id="open", tracker_id=None, limit=100):
        issue = self.open_issue if status_id == "open" else self.closed_issue
        if issue is None:
            return []
        return [{"id": issue.id, "subject": issue.subject, "status": {"name": issue.state}}]

    def get_project_metadata(self, project_id):
        return {
            "project": {"id": 1, "identifier": project_id, "name": "EMS"},
            "trackers": [{"id": 1, "name": "Bug"}],
            "priorities": [{"id": 5, "name": "L2"}],
            "custom_fields": [
                {"id": 119, "name": "Priority", "field_format": "list", "is_required": False}
            ],
            "statuses": [{"id": 1, "name": "New", "is_closed": False}],
        }

    def issue_url(self, issue_id):
        return f"https://redmine.example.com/issues/{issue_id}"

    def create_issue(self, payload):
        self.created_payloads.append(payload)
        self.last_issue_payload = dict(payload)
        if self.fail_create:
            raise RedmineMcpError("create failed with REDMINE_API_KEY=redmine-secret")
        issue = RedmineIssue(
            id="12345",
            url="https://redmine.example.com/issues/12345",
            subject=payload["subject"],
            reused=False,
        )
        self.open_issue = issue
        return issue

    def get_issue(self, issue_id):
        if self.issue_readback_override is not None:
            return self.issue_readback_override
        payload = self.last_issue_payload or {}
        return {
            "id": str(issue_id),
            "priority": {"id": payload.get("priority_id")},
            "custom_fields": payload.get("custom_fields") or [],
        }

    def upload_attachment(self, *, filename, content):
        self.uploaded_files.append((filename, content))
        if self.fail_upload:
            raise RedmineMcpError("upload failed with token=7167.upload-secret")
        return f"7167.upload-secret-{len(self.uploaded_files)}"

    def add_comment(self, issue_id, notes):
        self.comments.append((str(issue_id), notes))
        if self.fail_comment:
            raise RedmineMcpError("comment failed")
        self.journals.append({"id": str(len(self.journals) + 1), "notes": notes})
        return {}

    def get_issue_journals(self, issue_id):
        return list(self.journals)


def settings(environment: str = "sandbox", *, template_file: str = "") -> RedmineSettings:
    return RedmineSettings(
        url="https://redmine.example.com",
        api_key="redmine-secret",
        environment=environment,
        project_id="ems",
        template_file=template_file,
    )


def bug_args(**overrides):
    values = {
        "operation_id": "operation-12345",
        "environment": "sandbox",
        "subject": "[EMS-1] Automation failure",
        "description": "TestLink evidence\nDedupe Key: testlink-agent:abc123",
        "tracker_id": "1",
        "priority_id": "2",
        "dedupe_marker": "testlink-agent:abc123",
    }
    values.update(overrides)
    return values


def write_test_png(directory: str, *, name: str = "screenshot.png", marker: bytes = b"A") -> Path:
    path = Path(directory) / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + marker)
    return path


def write_format_template(
    directory: str,
    *,
    engine: str = "markdown",
    validation: str = "strict",
) -> Path:
    path = Path(directory) / "redmine-template.json"
    path.write_text(
        json.dumps(
            {
                "project_id": "ems",
                "tracker_id": 1,
                "priority_id": 2,
                "text_format": {
                    "engine": engine,
                    "validation": validation,
                    "policy_version": 1,
                },
                "custom_fields": [],
            }
        ),
        encoding="utf-8",
    )
    return path


class RedmineMcpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_env = {
            name: os.environ.get(name)
            for name in (
                "REDMINE_ALLOW_MANAGER_FIELDS",
                "REDMINE_API_KEY",
                "REDMINE_ENV",
                "REDMINE_MCP_ENV_FILE",
                "REDMINE_PROJECT_ID",
                "REDMINE_TEMPLATE",
                "REDMINE_URL",
                "TESTLINK_AGENT_ENV_FILE",
            )
        }
        for name in self.saved_env:
            os.environ.pop(name, None)

    def tearDown(self) -> None:
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_preview_bug_does_not_create_issue(self) -> None:
        client = FakeRedmineClient()
        with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
            result = api.redmine_preview_bug(**bug_args())

        self.assertTrue(result["ok"])
        preview = result["result"]
        self.assertEqual("preview", preview["mode"])
        self.assertEqual("create", preview["action"])
        self.assertTrue(preview["planned_write"])
        self.assertEqual(64, len(preview["preview_digest"]))
        self.assertEqual([], client.created_payloads)

    def test_corporate_markdown_preview_uses_template_contract(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template = write_format_template(tmpdir)
            with patch(
                "redmine_mcp.api._runtime",
                return_value=(settings("corp", template_file=str(template)), client),
            ):
                result = api.redmine_preview_bug(
                    **bug_args(environment="corp", description="# Environment\n\nValid Markdown text.")
                )

        self.assertTrue(result["ok"])
        preview = result["result"]
        self.assertEqual("create", preview["action"])
        self.assertTrue(preview["planned_write"])
        self.assertEqual("markdown", preview["text_format"]["engine"])
        self.assertTrue(preview["format_validation"]["valid"])
        self.assertEqual("AMBIGUOUS_HASH_LINE_IN_MARKDOWN", preview["format_validation"]["warnings"][0]["code"])

    def test_corporate_textile_heading_is_blocked_in_markdown_description(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template = write_format_template(tmpdir)
            with patch(
                "redmine_mcp.api._runtime",
                return_value=(settings("corp", template_file=str(template)), client),
            ):
                result = api.redmine_preview_bug(
                    **bug_args(environment="corp", description="h3. Environment\nEvidence")
                )

        self.assertTrue(result["ok"])
        preview = result["result"]
        self.assertEqual("blocked", preview["action"])
        self.assertFalse(preview["planned_write"])
        self.assertEqual("TEXTILE_HEADING_IN_MARKDOWN", preview["format_validation"]["errors"][0]["code"])
        self.assertEqual([], client.created_payloads)

    def test_markdown_code_fence_does_not_trigger_textile_heading_error(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template = write_format_template(tmpdir)
            with patch(
                "redmine_mcp.api._runtime",
                return_value=(settings("corp", template_file=str(template)), client),
            ):
                result = api.redmine_preview_bug(
                    **bug_args(environment="corp", description="```text\nh3. Environment\n|_. Header |\n```")
                )

        self.assertTrue(result["ok"])
        self.assertEqual("create", result["result"]["action"])
        self.assertTrue(result["result"]["format_validation"]["valid"])

    def test_text_format_change_invalidates_confirmed_bug_preview(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template = write_format_template(tmpdir)
            runtime = settings("corp", template_file=str(template))
            with patch("redmine_mcp.api._runtime", return_value=(runtime, client)):
                preview = api.redmine_preview_bug(
                    **bug_args(environment="corp", description="Valid text")
                )["result"]
                write_format_template(tmpdir, engine="textile")
                result = api.redmine_create_bug(
                    **bug_args(
                        environment="corp",
                        description="Valid text",
                        write=True,
                        preview_digest=preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )

        self.assertFalse(result["ok"])
        self.assertIn("preview_digest", result["error"]["error"]["message"])
        self.assertEqual([], client.created_payloads)

    def test_create_bug_requires_exact_preview_digest_and_writes_audit(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**bug_args())["result"]
                result = api.redmine_create_bug(
                    **bug_args(
                        write=True,
                        preview_digest=preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )

            self.assertTrue(result["ok"])
            self.assertEqual("created", result["result"]["action"])
            self.assertEqual(1, len(client.created_payloads))
            audit_path = Path(tmpdir) / result["result"]["audit_id"]
            self.assertTrue(audit_path.exists())
            audit_text = audit_path.read_text(encoding="utf-8")
            self.assertNotIn("redmine-secret", audit_text)
            audit = json.loads(audit_text)
            self.assertEqual("success", audit["status"])
            self.assertEqual(
                ["TEXT_FORMAT_NOT_CONFIGURED"],
                audit["format_validation"]["warning_codes"],
            )
            self.assertNotIn("excerpt", audit_text)

    def test_image_attachment_is_previewed_hashed_uploaded_and_audited_without_token(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            image = write_test_png(tmpdir)
            args = bug_args(
                attachments=[
                    {
                        "file": str(image),
                        "filename": "filter-result.png",
                        "description": "Filter result evidence",
                    }
                ]
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**args)["result"]
                self.assertEqual([], client.uploaded_files)
                self.assertEqual("upload-and-attach", preview["attachment_action"])
                self.assertEqual(1, preview["attachment_count"])
                self.assertEqual("image/png", preview["attachments"][0]["content_type"])
                self.assertEqual(64, len(preview["attachments"][0]["sha256"]))

                result = api.redmine_create_bug(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )

            self.assertTrue(result["ok"])
            self.assertEqual("attached", result["result"]["attachment_status"])
            self.assertEqual([("filter-result.png", image.read_bytes())], client.uploaded_files)
            upload = client.created_payloads[0]["uploads"][0]
            self.assertEqual("filter-result.png", upload["filename"])
            self.assertEqual("image/png", upload["content_type"])
            self.assertIn("token", upload)
            audit_text = (Path(tmpdir) / result["result"]["audit_id"]).read_text(encoding="utf-8")

        self.assertNotIn("upload-secret", audit_text)
        self.assertNotIn('"token"', audit_text)
        self.assertEqual("attached", json.loads(audit_text)["attachment_status"])

    def test_attachment_content_change_invalidates_preview_before_upload(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            image = write_test_png(tmpdir, marker=b"before")
            args = bug_args(attachments=[{"file": str(image)}])
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**args)["result"]
                image.write_bytes(b"\x89PNG\r\n\x1a\nafter")
                result = api.redmine_create_bug(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )

        self.assertFalse(result["ok"])
        self.assertIn("does not match", result["error"]["error"]["message"])
        self.assertEqual([], client.uploaded_files)
        self.assertEqual([], client.created_payloads)

    def test_non_image_attachment_is_rejected_during_preview(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            attachment = Path(tmpdir) / "evidence.txt"
            attachment.write_text("not an image", encoding="utf-8")
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                result = api.redmine_preview_bug(
                    **bug_args(attachments=[{"file": str(attachment)}])
                )

        self.assertFalse(result["ok"])
        self.assertEqual("ATTACHMENT_TYPE_UNSUPPORTED", result["error"]["error"]["code"])

    def test_attachment_upload_failure_is_audited_without_upload_token(self) -> None:
        client = FakeRedmineClient()
        client.fail_upload = True
        with TemporaryDirectory() as tmpdir:
            image = write_test_png(tmpdir)
            args = bug_args(attachments=[{"file": str(image)}])
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**args)["result"]
                result = api.redmine_create_bug(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )
            audit_path = next(Path(tmpdir).glob("*.json"))
            audit_text = audit_path.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual([], client.created_payloads)
        self.assertNotIn("7167.upload-secret", json.dumps(result))
        self.assertIn("*****", result["error"]["error"]["message"])
        self.assertNotIn("7167.upload-secret", audit_text)
        self.assertIn("*****", audit_text)
        self.assertEqual("failed", json.loads(audit_text)["status"])

    def test_reused_issue_does_not_upload_or_duplicate_image_attachment(self) -> None:
        client = FakeRedmineClient()
        client.open_issue = RedmineIssue(
            id="88",
            url="https://redmine.example.com/issues/88",
            subject="Existing",
            reused=True,
        )
        with TemporaryDirectory() as tmpdir:
            image = write_test_png(tmpdir)
            args = bug_args(attachments=[{"file": str(image)}])
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**args)["result"]
                result = api.redmine_create_bug(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )

        self.assertEqual("not-uploaded-reused", preview["attachment_action"])
        self.assertTrue(preview["warnings"])
        self.assertTrue(result["ok"])
        self.assertEqual("not-uploaded-reused", result["result"]["attachment_status"])
        self.assertEqual([], client.uploaded_files)
        self.assertEqual([], client.created_payloads)

    def test_changed_description_invalidates_preview_and_is_audited(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**bug_args())["result"]
                result = api.redmine_create_bug(
                    **bug_args(
                        description="Changed after preview\nDedupe Key: testlink-agent:abc123",
                        write=True,
                        preview_digest=preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )

            self.assertFalse(result["ok"])
            self.assertEqual("redmine-bug-write", result["error"]["error"]["stage"])
            self.assertEqual([], client.created_payloads)
            audits = list(Path(tmpdir).glob("*.json"))
            self.assertEqual(1, len(audits))
            self.assertEqual("failed", json.loads(audits[0].read_text(encoding="utf-8"))["status"])

    def test_external_create_failure_updates_started_audit_without_secret(self) -> None:
        client = FakeRedmineClient()
        client.fail_create = True
        os.environ["REDMINE_API_KEY"] = "redmine-secret"
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**bug_args())["result"]
                result = api.redmine_create_bug(
                    **bug_args(
                        write=True,
                        preview_digest=preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )

            audits = list(Path(tmpdir).glob("*.json"))
            self.assertEqual(1, len(audits))
            audit_text = audits[0].read_text(encoding="utf-8")
            audit = json.loads(audit_text)

        self.assertFalse(result["ok"])
        self.assertEqual("failed", audit["status"])
        self.assertNotIn("redmine-secret", audit_text)
        self.assertIn("*****", audit_text)

    def test_dedupe_cannot_be_disabled(self) -> None:
        client = FakeRedmineClient()
        with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
            result = api.redmine_preview_bug(**bug_args(dedupe="none"))

        self.assertFalse(result["ok"])
        self.assertEqual("DEDUPE_REQUIRED", result["error"]["error"]["code"])

    def test_open_dedupe_issue_is_reused_without_create(self) -> None:
        client = FakeRedmineClient()
        client.open_issue = RedmineIssue(
            id="88",
            url="https://redmine.example.com/issues/88",
            subject="Existing",
            reused=True,
        )
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**bug_args())["result"]
                result = api.redmine_create_bug(
                    **bug_args(
                        write=True,
                        preview_digest=preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )

        self.assertEqual("reuse", preview["action"])
        self.assertFalse(preview["planned_write"])
        self.assertTrue(result["ok"])
        self.assertEqual("reused", result["result"]["action"])
        self.assertTrue(result["result"]["issue"]["reused"])
        self.assertEqual([], client.created_payloads)

    def test_closed_dedupe_issue_blocks_write_and_never_reopens(self) -> None:
        client = FakeRedmineClient()
        client.closed_issue = RedmineIssue(
            id="99",
            url="https://redmine.example.com/issues/99",
            subject="Closed issue",
            state="closed",
        )
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**bug_args())["result"]
                result = api.redmine_create_bug(
                    **bug_args(
                        write=True,
                        preview_digest=preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )

        self.assertEqual("blocked", preview["action"])
        self.assertFalse(result["ok"])
        self.assertEqual([], client.created_payloads)

    def test_concurrent_state_change_requires_new_preview_and_prevents_duplicate(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**bug_args())["result"]
                client.open_issue = RedmineIssue(
                    id="77",
                    url="https://redmine.example.com/issues/77",
                    subject="Created by another actor",
                    reused=True,
                )
                result = api.redmine_create_bug(
                    **bug_args(
                        write=True,
                        preview_digest=preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )

        self.assertFalse(result["ok"])
        self.assertIn("does not match", result["error"]["error"]["message"])
        self.assertEqual([], client.created_payloads)

    def test_manager_fields_are_previewed_as_blocked_by_default(self) -> None:
        client = FakeRedmineClient()
        with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
            preview = api.redmine_preview_bug(**bug_args(assigned_to_id="123"))["result"]

        self.assertEqual("blocked", preview["action"])
        self.assertEqual(["assigned_to_id"], preview["blocked_fields"])

    def test_manager_fields_require_explicit_local_override(self) -> None:
        os.environ["REDMINE_ALLOW_MANAGER_FIELDS"] = "true"
        client = FakeRedmineClient()
        with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
            preview = api.redmine_preview_bug(**bug_args(assigned_to_id="123"))["result"]

        self.assertEqual("create", preview["action"])
        self.assertTrue(preview["manager_fields_enabled"])

    def test_comment_is_preview_first_and_digest_bound(self) -> None:
        client = FakeRedmineClient()
        args = {
            "operation_id": "operation-comment-1",
            "environment": "sandbox",
            "issue_id": "88",
            "notes": "Automation evidence only; no status change.",
        }
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_add_comment(**args)["result"]
                self.assertEqual([], client.comments)
                result = api.redmine_add_comment(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )

        self.assertTrue(result["ok"])
        self.assertEqual("added", result["result"]["status"])
        self.assertEqual([("88", args["notes"])], client.comments)

    def test_corporate_comment_blocks_textile_heading(self) -> None:
        client = FakeRedmineClient()
        args = {
            "operation_id": "operation-comment-format",
            "environment": "corp",
            "issue_id": "88",
            "notes": "h3. Retest evidence",
        }
        with TemporaryDirectory() as tmpdir:
            template = write_format_template(tmpdir)
            with patch(
                "redmine_mcp.api._runtime",
                return_value=(settings("corp", template_file=str(template)), client),
            ):
                preview = api.redmine_preview_comment(**args)["result"]
                result = api.redmine_add_comment(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )

        self.assertEqual("blocked", preview["action"])
        self.assertFalse(preview["planned_write"])
        self.assertEqual("TEXTILE_HEADING_IN_MARKDOWN", preview["format_validation"]["errors"][0]["code"])
        self.assertFalse(result["ok"])
        self.assertEqual("WRITE_BLOCKED", result["error"]["error"]["code"])
        self.assertEqual([], client.comments)

    def test_corporate_comment_allows_textile_examples_inside_code_fence(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template = write_format_template(tmpdir)
            with patch(
                "redmine_mcp.api._runtime",
                return_value=(settings("corp", template_file=str(template)), client),
            ):
                preview = api.redmine_preview_comment(
                    operation_id="operation-comment-code",
                    environment="corp",
                    issue_id="88",
                    notes="```text\nh3. Environment\n|_. Header |\n```",
                )["result"]

        self.assertEqual("add-comment", preview["action"])
        self.assertTrue(preview["planned_write"])
        self.assertTrue(preview["format_validation"]["valid"])

    def test_successful_comment_retry_is_idempotently_skipped(self) -> None:
        client = FakeRedmineClient()
        args = {
            "operation_id": "operation-comment-retry",
            "environment": "sandbox",
            "issue_id": "88",
            "notes": "Evidence",
        }
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_add_comment(**args)["result"]
                write_operation_audit(
                    {
                        "schema_version": "1.0",
                        "operation_id": args["operation_id"],
                        "environment": "sandbox",
                        "action": "add-comment",
                        "status": "added",
                        "preview_digest": preview["preview_digest"],
                    },
                    tmpdir,
                )
                result = api.redmine_add_comment(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )

        self.assertTrue(result["ok"])
        self.assertEqual("skipped-resume", result["result"]["status"])
        self.assertEqual([], client.comments)

    def test_started_comment_recovers_matching_journal(self) -> None:
        client = FakeRedmineClient()
        client.journals.append({"id": "7", "notes": "Evidence"})
        args = {
            "operation_id": "operation-comment-recover",
            "environment": "sandbox",
            "issue_id": "88",
            "notes": "Evidence",
        }
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_add_comment(**args)["result"]
                audit_path = write_operation_audit(
                    {
                        "schema_version": "1.0",
                        "operation_id": args["operation_id"],
                        "environment": "sandbox",
                        "action": "add-comment",
                        "status": "started",
                        "preview_digest": preview["preview_digest"],
                    },
                    tmpdir,
                )
                result = api.redmine_add_comment(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )
                recovered = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual("skipped-resume", result["result"]["status"])
        self.assertTrue(recovered["recovered"])
        self.assertEqual([], client.comments)

    def test_started_comment_fails_closed_without_matching_journal(self) -> None:
        client = FakeRedmineClient()
        args = {
            "operation_id": "operation-comment-indeterminate",
            "environment": "sandbox",
            "issue_id": "88",
            "notes": "Evidence",
        }
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_add_comment(**args)["result"]
                write_operation_audit(
                    {
                        "schema_version": "1.0",
                        "operation_id": args["operation_id"],
                        "environment": "sandbox",
                        "action": "add-comment",
                        "status": "started",
                        "preview_digest": preview["preview_digest"],
                    },
                    tmpdir,
                )
                result = api.redmine_add_comment(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )

        self.assertFalse(result["ok"])
        self.assertEqual("INDETERMINATE_OPERATION", result["error"]["error"]["code"])
        self.assertEqual([], client.comments)

    def test_environment_mismatch_fails_before_client_action(self) -> None:
        client = FakeRedmineClient()
        with patch("redmine_mcp.api._runtime", return_value=(settings("corp"), client)):
            result = api.redmine_preview_bug(**bug_args(environment="sandbox"))

        self.assertFalse(result["ok"])
        self.assertEqual("ENVIRONMENT_MISMATCH", result["error"]["error"]["code"])
        self.assertEqual([], client.created_payloads)

    def test_config_requires_explicit_environment(self) -> None:
        os.environ["REDMINE_URL"] = "https://redmine.example.com"
        os.environ["REDMINE_API_KEY"] = "redmine-secret"
        with self.assertRaisesRegex(RedmineMcpError, "explicitly set"):
            load_redmine_settings()

    def test_config_supports_utf8_bom_on_environment_key(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "redmine.env"
            env_file.write_text(
                "REDMINE_ENV=sandbox\n"
                "REDMINE_URL=https://redmine.example.com\n"
                "REDMINE_API_KEY=replace-with-redmine-key\n",
                encoding="utf-8-sig",
            )

            loaded = load_redmine_settings(env_file=str(env_file))

        self.assertEqual("sandbox", loaded.environment)
        self.assertEqual("https://redmine.example.com", loaded.url)

    def test_config_loads_default_template_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            template = write_format_template(tmpdir)
            env_file = Path(tmpdir) / "redmine.env"
            env_file.write_text(
                "REDMINE_ENV=sandbox\n"
                "REDMINE_URL=https://redmine.example.com\n"
                "REDMINE_API_KEY=replace-with-redmine-key\n"
                f"REDMINE_TEMPLATE={template}\n",
                encoding="utf-8",
            )

            loaded = load_redmine_settings(env_file=str(env_file))

        self.assertEqual(str(template), loaded.template_file)

    def test_health_and_search_return_safe_summary(self) -> None:
        client = FakeRedmineClient()
        client.open_issue = RedmineIssue(
            id="88",
            url="https://redmine.example.com/issues/88",
            subject="Existing",
        )
        with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
            health = api.redmine_health(operation_id="operation-health", environment="sandbox")
            search = api.redmine_search_issues(operation_id="operation-search", environment="sandbox")

        self.assertTrue(health["result"]["authenticated"])
        self.assertEqual("qa-user", health["result"]["login"])
        self.assertEqual(1, search["result"]["issue_count"])

    def test_project_metadata_returns_safe_field_summary(self) -> None:
        client = FakeRedmineClient()
        with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
            result = api.redmine_get_project_metadata(
                operation_id="operation-metadata",
                environment="sandbox",
            )

        self.assertTrue(result["ok"])
        self.assertEqual("EMS", result["result"]["project"]["name"])
        self.assertEqual("Priority", result["result"]["custom_fields"][0]["name"])
        self.assertNotIn("possible_values", result["result"]["custom_fields"][0])

    def test_template_validation_and_rendered_template_bug_preview(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "priority_id": 2,
                        "required_custom_fields": ["Severity"],
                        "custom_fields": [{"id": 10, "name": "Severity", "value": "Major"}],
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                validation = api.redmine_validate_template(
                    operation_id="operation-template",
                    environment="sandbox",
                    template_file=str(template_path),
                )
                preview = api.redmine_preview_bug(
                    **bug_args(
                        tracker_id=None,
                        priority_id=None,
                        template_file=str(template_path),
                    )
                )

        self.assertTrue(validation["ok"])
        self.assertTrue(validation["result"]["valid"])
        self.assertTrue(preview["ok"])
        self.assertEqual("create", preview["result"]["action"])

    def test_company_field_contract_resolves_severity_and_previews_distinct_priority(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "severity": {
                            "transport_field": "priority_id",
                            "default": "L2",
                            "values": {"L3": 4, "L2": 5, "L1": 6},
                        },
                        "priority": {
                            "transport_field": "custom_fields",
                            "custom_field_id": 119,
                            "allowed_values": [],
                            "default": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_preview_bug(
                    **bug_args(
                        tracker_id=None,
                        priority_id=None,
                        template_file=str(template_path),
                    )
                )["result"]

        self.assertEqual("L2", preview["issue_fields"]["severity"]["label"])
        self.assertEqual("5", preview["issue_fields"]["severity"]["priority_id"])
        self.assertEqual("L2 (Redmine priority_id=5)", preview["issue_fields"]["severity"]["display"])
        self.assertEqual("blank (custom field ID 119)", preview["issue_fields"]["priority"]["display"])
        self.assertEqual("119", preview["issue_fields"]["priority"]["custom_field_id"])
        self.assertEqual("5", preview["issue_payload"]["priority_id"])
        self.assertNotIn("custom_fields", preview["issue_payload"])

    def test_company_field_contract_rejects_wrong_raw_severity_id(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "severity": {
                            "transport_field": "priority_id",
                            "default": "L2",
                            "values": {"L3": 4, "L2": 5, "L1": 6},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                result = api.redmine_preview_bug(
                    **bug_args(priority_id="4", template_file=str(template_path))
                )

        self.assertFalse(result["ok"])
        self.assertEqual("SEVERITY_PRIORITY_CONFLICT", result["error"]["error"]["code"])

    def test_unconfirmed_custom_priority_value_is_rejected(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "severity": {
                            "transport_field": "priority_id",
                            "default": "L2",
                            "values": {"L3": 4, "L2": 5, "L1": 6},
                        },
                        "priority": {
                            "transport_field": "custom_fields",
                            "custom_field_id": 119,
                            "allowed_values": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                result = api.redmine_preview_bug(
                    **bug_args(
                        priority_id=None,
                        custom_priority="L2",
                        template_file=str(template_path),
                    )
                )

        self.assertFalse(result["ok"])
        self.assertEqual("INVALID_CUSTOM_PRIORITY", result["error"]["error"]["code"])

    def test_template_validation_rejects_unconfirmed_direct_priority_field_value(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "severity": {
                            "transport_field": "priority_id",
                            "default": "L2",
                            "values": {"L3": 4, "L2": 5, "L1": 6},
                        },
                        "priority": {
                            "transport_field": "custom_fields",
                            "custom_field_id": 119,
                            "allowed_values": [],
                        },
                        "custom_fields": [{"id": 119, "name": "Priority", "value": "L2"}],
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                result = api.redmine_validate_template(
                    operation_id="operation-template-priority",
                    environment="sandbox",
                    template_file=str(template_path),
                )

        self.assertFalse(result["ok"])
        self.assertEqual("INVALID_CUSTOM_PRIORITY", result["error"]["error"]["code"])

    def test_template_validation_rejects_duplicate_custom_field_ids(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "priority_id": 5,
                        "custom_fields": [
                            {"id": 119, "name": "Priority", "value": ""},
                            {"id": 119, "name": "Priority Alias", "value": ""},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                result = api.redmine_validate_template(
                    operation_id="operation-template-duplicate-id",
                    environment="sandbox",
                    template_file=str(template_path),
                )

        self.assertFalse(result["ok"])
        self.assertEqual("TEMPLATE_INVALID", result["error"]["error"]["code"])

    def test_semantic_fields_change_preview_digest_and_route_priority_to_field_119(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "severity": {
                            "transport_field": "priority_id",
                            "default": "L2",
                            "values": {"L3": 4, "L2": 5, "L1": 6},
                        },
                        "priority": {
                            "transport_field": "custom_fields",
                            "custom_field_id": 119,
                            "allowed_values": ["P1", "P2"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                first = api.redmine_preview_bug(
                    **bug_args(
                        priority_id=None,
                        severity="L2",
                        custom_priority="P1",
                        template_file=str(template_path),
                    )
                )["result"]
                second = api.redmine_preview_bug(
                    **bug_args(
                        priority_id=None,
                        severity="L1",
                        custom_priority="P1",
                        template_file=str(template_path),
                    )
                )["result"]
                third = api.redmine_preview_bug(
                    **bug_args(
                        priority_id=None,
                        severity="L2",
                        custom_priority="P2",
                        template_file=str(template_path),
                    )
                )["result"]

        self.assertEqual("5", first["issue_payload"]["priority_id"])
        self.assertEqual([{"id": "119", "value": "P1"}], first["issue_payload"]["custom_fields"])
        self.assertNotEqual(first["preview_digest"], second["preview_digest"])
        self.assertNotEqual(first["preview_digest"], third["preview_digest"])

    def test_created_company_issue_readback_verifies_severity_and_blank_priority(self) -> None:
        client = FakeRedmineClient()
        client.issue_readback_override = {
            "id": "12345",
            "priority": {"id": 5, "name": "L2"},
            "custom_fields": [{"id": 119, "name": "Priority", "value": ""}],
        }
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "severity": {
                            "transport_field": "priority_id",
                            "default": "L2",
                            "values": {"L3": 4, "L2": 5, "L1": 6},
                        },
                        "priority": {
                            "transport_field": "custom_fields",
                            "custom_field_id": 119,
                            "allowed_values": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = bug_args(
                tracker_id=None,
                priority_id=None,
                template_file=str(template_path),
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**args)["result"]
                result = api.redmine_create_bug(
                    **args,
                    write=True,
                    preview_digest=preview["preview_digest"],
                    audit_dir=tmpdir,
                )

        self.assertTrue(result["ok"])
        verification = result["result"]["field_verification"]
        self.assertEqual("verified", verification["status"])
        self.assertTrue(verification["severity"]["match"])
        self.assertTrue(verification["priority"]["match"])
        self.assertEqual("", verification["priority"]["actual_value"])

    def test_created_issue_readback_mismatch_fails_and_audits_issue_identity(self) -> None:
        client = FakeRedmineClient()
        client.issue_readback_override = {
            "id": "12345",
            "priority": {"id": 4, "name": "L3"},
            "custom_fields": [],
        }
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                preview = api.redmine_create_bug(**bug_args())["result"]
                result = api.redmine_create_bug(
                    **bug_args(
                        write=True,
                        preview_digest=preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )
            audit_files = list(Path(tmpdir).glob("*.json"))
            audit = json.loads(audit_files[0].read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        self.assertEqual("VERIFICATION_FAILED", result["error"]["error"]["code"])
        self.assertEqual("12345", result["error"]["partial_result"]["issue"]["id"])
        self.assertEqual(
            "verification_failed",
            result["error"]["partial_result"]["field_verification"]["status"],
        )
        self.assertEqual("12345", audit["issue"]["id"])
        self.assertEqual("verification-failed", audit["status"])
        self.assertEqual("verification_failed", audit["field_verification"]["status"])

    def test_verification_failure_retry_reuses_issue_without_duplicate_create(self) -> None:
        client = FakeRedmineClient()
        client.issue_readback_override = {
            "id": "12345",
            "priority": {"id": 4, "name": "L3"},
            "custom_fields": [],
        }
        with TemporaryDirectory() as tmpdir:
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                first_preview = api.redmine_create_bug(**bug_args())["result"]
                failed = api.redmine_create_bug(
                    **bug_args(
                        write=True,
                        preview_digest=first_preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )
                retry_preview = api.redmine_create_bug(**bug_args())["result"]
                retried = api.redmine_create_bug(
                    **bug_args(
                        write=True,
                        preview_digest=retry_preview["preview_digest"],
                        audit_dir=tmpdir,
                    )
                )

        self.assertFalse(failed["ok"])
        self.assertEqual("reuse", retry_preview["action"])
        self.assertTrue(retried["ok"])
        self.assertEqual("reused", retried["result"]["action"])
        self.assertEqual(1, len(client.created_payloads))

    def test_template_accepts_object_required_field_descriptors(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "priority_id": 2,
                        "required_custom_fields": [{"id": 10, "name": "Severity"}],
                        "custom_fields": [{"id": 10, "name": "Severity", "value": "Major"}],
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                validation = api.redmine_validate_template(
                    operation_id="operation-template-object-required",
                    environment="sandbox",
                    template_file=str(template_path),
                )

        self.assertTrue(validation["ok"])
        self.assertEqual(["Severity"], validation["result"]["required_custom_fields"])

    def test_unresolved_template_tokens_fail_before_preview(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "priority_id": 2,
                        "custom_fields": [{"id": 10, "name": "FW Ver", "value": "{{context.build}}"}],
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                result = api.redmine_preview_bug(
                    **bug_args(
                        tracker_id=None,
                        priority_id=None,
                        template_file=str(template_path),
                    )
                )

        self.assertFalse(result["ok"])
        self.assertEqual("TEMPLATE_UNRESOLVED", result["error"]["error"]["code"])

    def test_template_cannot_set_status(self) -> None:
        client = FakeRedmineClient()
        with TemporaryDirectory() as tmpdir:
            template_path = Path(tmpdir) / "redmine-template.json"
            template_path.write_text(
                json.dumps(
                    {
                        "project_id": "ems",
                        "tracker_id": 1,
                        "priority_id": 2,
                        "status_id": 3,
                    }
                ),
                encoding="utf-8",
            )
            with patch("redmine_mcp.api._runtime", return_value=(settings(), client)):
                result = api.redmine_validate_template(
                    operation_id="operation-template-status",
                    environment="sandbox",
                    template_file=str(template_path),
                )

        self.assertFalse(result["ok"])
        self.assertEqual("RESTRICTED_FIELD", result["error"]["error"]["code"])


class RedmineClientTests(unittest.TestCase):
    def test_upload_attachment_uses_binary_body_filename_query_and_returns_token(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"upload":{"token":"7167.server-secret"}}'

        captured = []

        def fake_urlopen(request, timeout):
            captured.append((request, timeout))
            return FakeResponse()

        client = RedmineClient("https://redmine.example.com", "redmine-secret", timeout=17)
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            token = client.upload_attachment(filename="filter result.png", content=b"PNG bytes")

        request, timeout = captured[0]
        self.assertEqual("7167.server-secret", token)
        self.assertEqual(17, timeout)
        self.assertEqual("POST", request.method)
        self.assertEqual(b"PNG bytes", request.data)
        self.assertIn("/uploads.json?filename=filter+result.png", request.full_url)
        self.assertEqual("application/octet-stream", request.get_header("Content-type"))

    def test_add_comment_uses_notes_only_payload(self) -> None:
        class RecordingClient(RedmineClient):
            def __init__(self):
                super().__init__("https://redmine.example.com", "redmine-secret")
                self.calls = []

            def request_json(self, method, path, payload=None, query=None):
                self.calls.append((method, path, payload, query))
                return {}

        client = RecordingClient()
        client.add_comment("123", "Evidence")

        self.assertEqual(
            [("PUT", "/issues/123.json", {"issue": {"notes": "Evidence"}}, None)],
            client.calls,
        )


if __name__ == "__main__":
    unittest.main()
