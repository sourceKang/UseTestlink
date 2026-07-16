import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core.audit import (
    append_audit_error,
    append_audit_result,
    build_audit_record,
    file_sha256,
    finalize_audit_record,
    read_audit_record,
    resume_item_has_testlink_success,
    resume_item_needs_redmine_comment,
    resume_items_by_external_id,
    validate_resume_audit,
    write_audit_record,
)
from testlink_agent_core.errors import TestLinkError


class AuditTests(unittest.TestCase):
    def test_file_sha256_hashes_report(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "report.txt"
            path.write_bytes(b"hello\n")

            self.assertEqual(
                file_sha256(path),
                "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03",
            )

    def test_build_record_includes_schema_and_report_hash(self):
        with TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / "report.txt"
            report.write_text("result\n", encoding="utf-8")

            record = build_audit_record(
                operation="upload-report",
                mode="write",
                report_path=report,
                profile={"testlink": "corp", "redmine": "corp"},
                testlink_target={"project": "EMS"},
                redmine_target={"enabled": True},
                report_schema="legacy-web-ems-report-v1",
                parsed_count=2,
                write_count=1,
                started_at="2026-07-06T00:00:00+00:00",
            )

        self.assertEqual(record["schema_version"], "1.0")
        self.assertEqual(record["operation"], "upload-report")
        self.assertEqual(record["profile"], {"testlink": "corp", "redmine": "corp"})
        self.assertEqual(record["testlink_target"], {"project": "EMS"})
        self.assertEqual(record["redmine_target"], {"enabled": True})
        self.assertEqual(record["report_schema"], "legacy-web-ems-report-v1")
        self.assertEqual(record["parsed_count"], 2)
        self.assertEqual(record["write_count"], 1)
        self.assertIn("report_sha256", record)
        self.assertEqual(record["results"], [])
        self.assertEqual(record["errors"], [])

    def test_append_and_finalize_record(self):
        record = build_audit_record(operation="upload-report", mode="write")

        append_audit_result(record, {"external_id": "EMS1-1", "status": "p"})
        append_audit_error(record, {"external_id": "EMS1-2", "stage": "redmine"})
        finalize_audit_record(record)

        self.assertEqual(record["results"], [{"external_id": "EMS1-1", "status": "p"}])
        self.assertEqual(record["errors"], [{"external_id": "EMS1-2", "stage": "redmine"}])
        self.assertEqual(record["status"], "failed")
        self.assertIn("finished_at", record)

    def test_write_audit_record_uses_atomic_json_file(self):
        with TemporaryDirectory() as tmpdir:
            record = build_audit_record(
                operation="upload-report",
                mode="write",
                started_at="2026-07-06T01:02:03+00:00",
            )
            finalize_audit_record(record, status="success")

            path = write_audit_record(record, Path(tmpdir))

            self.assertTrue(path.exists())
            self.assertEqual(path.name, "20260706T010203Z0000-upload-report.json")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["schema_version"], "1.0")
            self.assertEqual(loaded["status"], "success")

    def test_write_audit_record_redacts_secret_values(self):
        saved = {
            "TESTLINK_DEVKEY": os.environ.get("TESTLINK_DEVKEY"),
            "REDMINE_API_KEY": os.environ.get("REDMINE_API_KEY"),
        }
        os.environ["TESTLINK_DEVKEY"] = "testlink-secret"
        os.environ["REDMINE_API_KEY"] = "redmine-secret"
        try:
            with TemporaryDirectory() as tmpdir:
                record = build_audit_record(
                    operation="upload-report",
                    mode="write",
                    started_at="2026-07-06T01:02:03+00:00",
                )
                append_audit_result(
                    record,
                    {
                        "devKey": "testlink-secret",
                        "redmine_api_key": "redmine-secret",
                        "password": "plain-password",
                        "notes": "contains testlink-secret and redmine-secret",
                    },
                )
                finalize_audit_record(record, status="success")

                path = write_audit_record(record, Path(tmpdir))
                text = path.read_text(encoding="utf-8")

            self.assertNotIn("testlink-secret", text)
            self.assertNotIn("redmine-secret", text)
            self.assertNotIn("plain-password", text)
            self.assertIn("*****", text)
            self.assertEqual(record["results"][0]["devKey"], "*****")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_read_audit_record_validates_schema(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.json"
            path.write_text(json.dumps({"schema_version": "0.9"}), encoding="utf-8")

            with self.assertRaisesRegex(TestLinkError, "Unsupported resume audit schema"):
                read_audit_record(path)

    def test_validate_resume_audit_rejects_mismatched_target(self):
        resume = {
            "schema_version": "1.0",
            "operation": "upload-report",
            "report_sha256": "abc",
            "report_schema": "legacy-web-ems-report-v1",
            "profile": {"testlink": "corp", "redmine": "corp"},
            "testlink_target": {"project": "EMS"},
        }
        current = {
            **resume,
            "testlink_target": {"project": "Other"},
        }

        with self.assertRaisesRegex(TestLinkError, "testlink_target"):
            validate_resume_audit(resume, current)

    def test_resume_item_helpers_index_success_and_comment_retry(self):
        record = {
            "results": [
                {
                    "external_id": "EMS1-1",
                    "testlink_write": "success",
                    "redmine_comment": "failed",
                },
                {
                    "external_id": "EMS1-2",
                    "testlink_write": "failed",
                },
            ]
        }

        indexed = resume_items_by_external_id(record)

        self.assertTrue(resume_item_has_testlink_success(indexed["EMS1-1"]))
        self.assertTrue(resume_item_needs_redmine_comment(indexed["EMS1-1"]))
        self.assertFalse(resume_item_has_testlink_success(indexed["EMS1-2"]))


if __name__ == "__main__":
    unittest.main()
