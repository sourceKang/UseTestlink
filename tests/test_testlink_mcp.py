from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from testlink_agent_core.config import TestLinkSettings
from testlink_agent_core.errors import TestLinkError
from testlink_mcp import api
from testlink_mcp.audit import write_operation_audit
from testlink_mcp.config import load_runtime


def legacy_preview(status: str = "f", notes: str = "Evidence") -> dict:
    return {
        "mode": "preview",
        "target": {
            "project": {"id": "10", "name": "EMS"},
            "test_plan": {"id": "20", "name": "Regression"},
            "platform": {"id": "30", "name": "Default Platform"},
            "build": {"id": "40", "name": "build-1"},
        },
        "payload": {
            "testcaseexternalid": "EMS-1",
            "testplanid": "20",
            "platformid": "30",
            "buildid": "40",
            "status": status,
            "notes": notes,
        },
    }


def args(**overrides):
    values = {
        "operation_id": "operation-testlink-1",
        "environment": "sandbox",
        "project": "EMS",
        "plan": "Regression",
        "platform": "Default Platform",
        "build": "build-1",
        "testcase_external_id": "EMS-1",
        "status": "f",
        "notes": "Evidence",
    }
    values.update(overrides)
    return values


class FakeWriteClient:
    def __init__(self, *, fail: bool = False, last_execution=None):
        self.fail = fail
        self.last_execution = last_execution
        self.payloads = []
        self.last_queries = []

    def report_result(self, payload):
        self.payloads.append(payload)
        if self.fail:
            raise TestLinkError("write failed with TESTLINK_DEVKEY=testlink-secret")
        return {"execution_id": "9001"}

    def get_last_execution_result(self, **kwargs):
        self.last_queries.append(kwargs)
        return self.last_execution


class TestLinkMcpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_env = {
            key: os.environ.get(key)
            for key in ("TESTLINK_AGENT_PROFILE", "TESTLINK_DEVKEY", "TESTLINK_URL")
        }
        for key in self.saved_env:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_execution_is_preview_only_by_default(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient()
        with patch("testlink_mcp.api.load_runtime", return_value=runtime):
            with patch("testlink_mcp.api._legacy_preview", return_value=legacy_preview()):
                with patch("testlink_mcp.api.write_client", return_value=client):
                    result = api.testlink_report_execution(**args())

        self.assertTrue(result["ok"])
        preview = result["result"]
        self.assertEqual("preview", preview["mode"])
        self.assertTrue(preview["planned_write"])
        self.assertEqual("Default Platform", preview["target"]["platform"]["name"])
        self.assertEqual(64, len(preview["preview_digest"]))
        self.assertEqual([], client.payloads)

    def test_exact_preview_digest_allows_append_and_updates_audit(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient()
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_preview", return_value=legacy_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_report_execution(**args())["result"]
                        result = api.testlink_report_execution(
                            **args(
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )

            audit_path = Path(tmpdir) / result["result"]["audit_id"]
            audit = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual("9001", result["result"]["execution_id"])
        self.assertEqual(1, len(client.payloads))
        self.assertEqual("success", audit["status"])

    def test_changed_payload_rejects_write_and_creates_failure_audit(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient()
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_preview", side_effect=[legacy_preview(), legacy_preview(status="p")]):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_report_execution(**args())["result"]
                        result = api.testlink_report_execution(
                            **args(
                                status="p",
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )

            audits = list(Path(tmpdir).glob("*.json"))

        self.assertFalse(result["ok"])
        self.assertEqual([], client.payloads)
        self.assertEqual(1, len(audits))

    def test_testlink_failure_updates_started_audit_and_redacts_secret(self) -> None:
        os.environ["TESTLINK_DEVKEY"] = "testlink-secret"
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient(fail=True)
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_preview", return_value=legacy_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_report_execution(**args())["result"]
                        result = api.testlink_report_execution(
                            **args(
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )

            audits = list(Path(tmpdir).glob("*.json"))
            audit_text = audits[0].read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(1, len(audits))
        self.assertNotIn("testlink-secret", audit_text)
        self.assertIn("*****", audit_text)

    def test_successful_operation_is_idempotently_skipped_on_retry(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient()
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_preview", return_value=legacy_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_report_execution(**args())["result"]
                        write_operation_audit(
                            {
                                "schema_version": "1.0",
                                "operation_id": args()["operation_id"],
                                "environment": "sandbox",
                                "action": "append-execution",
                                "status": "success",
                                "preview_digest": preview["preview_digest"],
                                "execution_id": "existing-9001",
                            },
                            tmpdir,
                        )
                        result = api.testlink_report_execution(
                            **args(
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )

        self.assertTrue(result["ok"])
        self.assertEqual("skipped-resume", result["result"]["status"])
        self.assertEqual("existing-9001", result["result"]["execution_id"])
        self.assertEqual([], client.payloads)

    def test_started_operation_recovers_matching_last_execution(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient(last_execution={"id": "recovered-1", "status": "f", "notes": "Evidence"})
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_preview", return_value=legacy_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_report_execution(**args())["result"]
                        audit_path = write_operation_audit(
                            {
                                "schema_version": "1.0",
                                "operation_id": args()["operation_id"],
                                "environment": "sandbox",
                                "action": "append-execution",
                                "status": "started",
                                "preview_digest": preview["preview_digest"],
                                "notes_digest": hashlib.sha256(b"Evidence").hexdigest(),
                            },
                            tmpdir,
                        )
                        result = api.testlink_report_execution(
                            **args(
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )
                        recovered_audit = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual("skipped-resume", result["result"]["status"])
        self.assertEqual("recovered-1", result["result"]["execution_id"])
        self.assertTrue(recovered_audit["recovered"])
        self.assertEqual([], client.payloads)

    def test_started_operation_fails_closed_when_last_execution_cannot_be_proven(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient(last_execution={"id": "other", "status": "p", "notes": "Other"})
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_preview", return_value=legacy_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_report_execution(**args())["result"]
                        write_operation_audit(
                            {
                                "schema_version": "1.0",
                                "operation_id": args()["operation_id"],
                                "environment": "sandbox",
                                "action": "append-execution",
                                "status": "started",
                                "preview_digest": preview["preview_digest"],
                                "notes_digest": hashlib.sha256(b"Evidence").hexdigest(),
                            },
                            tmpdir,
                        )
                        result = api.testlink_report_execution(
                            **args(
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )

        self.assertFalse(result["ok"])
        self.assertIn("refusing to append", result["error"]["error"]["message"])
        self.assertEqual([], client.payloads)

    def test_environment_mismatch_fails_before_write(self) -> None:
        runtime = SimpleNamespace(environment="corp")
        with patch("testlink_mcp.api.load_runtime", return_value=runtime):
            result = api.testlink_report_execution(**args(environment="sandbox"))

        self.assertFalse(result["ok"])

    def test_runtime_requires_explicit_profile(self) -> None:
        settings = TestLinkSettings(url="https://testlink.example.com", devkey="secret")
        with patch("testlink_mcp.config.load_testlink_settings", return_value=settings):
            with self.assertRaisesRegex(TestLinkError, "explicitly set"):
                load_runtime()


if __name__ == "__main__":
    unittest.main()
