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
    def __init__(self, *, fail: bool = False, last_execution=None, testcase_readback=None, response=None):
        self.fail = fail
        self.last_execution = last_execution
        self.testcase_readback = testcase_readback
        self.response = response if response is not None else {"execution_id": "9001"}
        self.payloads = []
        self.testcase_payloads = []
        self.last_queries = []

    def report_result(self, payload):
        self.payloads.append(payload)
        if self.fail:
            raise TestLinkError("write failed with TESTLINK_DEVKEY=testlink-secret")
        return self.response

    def get_last_execution_result(self, **kwargs):
        self.last_queries.append(kwargs)
        return self.last_execution

    def create_test_case(self, payload):
        self.testcase_payloads.append(("create", payload))
        if self.fail:
            raise TestLinkError("write failed with TESTLINK_DEVKEY=testlink-secret")
        return {"id": "501"}

    def update_test_case(self, payload):
        self.testcase_payloads.append(("update", payload))
        if self.fail:
            raise TestLinkError("write failed with TESTLINK_DEVKEY=testlink-secret")
        return {"id": payload.get("testcaseid") or "501"}

    def get_test_case(self, **kwargs):
        self.last_queries.append(kwargs)
        return self.testcase_readback

    def get_suite_cases(self, testsuite_id, *, deep=False, details="full"):
        return [{"id": "501", "name": "Filter case", "version": "1"}]


def testcase_preview(*, multi_row: bool = False, summary: str | None = None) -> dict:
    if multi_row:
        steps = [
            {"step_number": 1, "actions": "Filter page", "expected_results": "Fields shown", "execution_type": 1},
            {"step_number": 2, "actions": "Apply", "expected_results": "Devices filtered", "execution_type": 1},
        ]
    else:
        steps = [
            {
                "step_number": 1,
                "actions": "1. Filter page<br />\n2. Apply",
                "expected_results": "1. Fields shown<br />\n2. Devices filtered",
                "execution_type": 1,
            }
        ]
    payload = {"testcaseexternalid": "EMS-1", "steps": steps}
    if summary is not None:
        payload["summary"] = summary
    return {
        "mode": "preview",
        "payload": payload,
    }


def testcase_args(**overrides):
    values = {
        "operation_id": "operation-testcase-1",
        "environment": "sandbox",
        "testcase_external_id": "EMS-1",
        "steps": [
            "Filter page => Fields shown",
            "Apply => Devices filtered",
        ],
    }
    values.update(overrides)
    return values


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

    def test_xmlrpc_list_response_records_the_execution_id(self) -> None:
        # TestLink answers reportTCResult with a list of structs, not a mapping.
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient(
            response=[{"status": True, "operation": "reportTCResult", "message": "Success!", "id": 3043053}]
        )
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_preview", return_value=legacy_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_report_execution(**args())["result"]
                        result = api.testlink_report_execution(
                            **args(write=True, preview_digest=preview["preview_digest"], audit_dir=tmpdir)
                        )

            audit = json.loads((Path(tmpdir) / result["result"]["audit_id"]).read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual("3043053", result["result"]["execution_id"])
        self.assertEqual("3043053", str(audit["execution_id"]))
        self.assertEqual("success", audit["status"])

    def test_response_without_execution_id_is_not_recorded_as_success(self) -> None:
        # A plan whose platform is ambiguous is answered with the platform list,
        # which must not be filed as a completed execution.
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient(response=[{"49": "NetAtlas EMS", "48": "OLT1408A"}])
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_preview", return_value=legacy_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_report_execution(**args())["result"]
                        result = api.testlink_report_execution(
                            **args(write=True, preview_digest=preview["preview_digest"], audit_dir=tmpdir)
                        )

            audits = [json.loads(path.read_text(encoding="utf-8")) for path in Path(tmpdir).glob("*.json")]

        self.assertFalse(result["ok"])
        self.assertIn("did not record the execution", result["error"]["error"]["message"])
        self.assertEqual(["failed"], [audit["status"] for audit in audits])

    def test_error_struct_response_is_not_recorded_as_success(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient(response=[{"code": 2000, "message": "(reportTCResult) - Invalid build id"}])
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_preview", return_value=legacy_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_report_execution(**args())["result"]
                        result = api.testlink_report_execution(
                            **args(write=True, preview_digest=preview["preview_digest"], audit_dir=tmpdir)
                        )

            audits = [json.loads(path.read_text(encoding="utf-8")) for path in Path(tmpdir).glob("*.json")]

        self.assertFalse(result["ok"])
        self.assertIn("Invalid build id", result["error"]["error"]["message"])
        self.assertEqual(["failed"], [audit["status"] for audit in audits])

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

    def test_testcase_preview_is_single_row_by_default(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        with patch("testlink_mcp.api.load_runtime", return_value=runtime):
            with patch("testlink_mcp.api._legacy_tool_preview", return_value=testcase_preview()):
                result = api.testlink_update_testcase(**testcase_args())

        self.assertTrue(result["ok"])
        preview = result["result"]
        self.assertEqual("single-row", preview["row_validation"]["row_policy"])
        self.assertEqual(1, preview["row_validation"]["planned_row_count"])
        self.assertEqual(2, preview["row_validation"]["logical_step_count"])

    def test_testcase_rejects_multi_row_without_explicit_authorization(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        with patch("testlink_mcp.api.load_runtime", return_value=runtime):
            with patch("testlink_mcp.api._legacy_tool_preview") as legacy_preview:
                result = api.testlink_update_testcase(**testcase_args(single_step=False))

        self.assertFalse(result["ok"])
        self.assertEqual("MULTI_ROW_NOT_AUTHORIZED", result["error"]["error"]["code"])
        legacy_preview.assert_not_called()

    def test_testcase_allows_explicitly_authorized_multi_row_preview(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        with patch("testlink_mcp.api.load_runtime", return_value=runtime):
            with patch("testlink_mcp.api._legacy_tool_preview", return_value=testcase_preview(multi_row=True)):
                result = api.testlink_update_testcase(
                    **testcase_args(single_step=False, allow_multi_row=True)
                )

        self.assertTrue(result["ok"])
        self.assertEqual("multi-row-authorized", result["result"]["row_validation"]["row_policy"])
        self.assertEqual(2, result["result"]["row_validation"]["planned_row_count"])

    def test_testcase_write_requires_digest_and_verifies_readback(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        readback = {
            "testcaseexternalid": "EMS-1",
            "steps": [
                {
                    "actions": "1. Filter page\n2. Apply",
                    "expected_results": "1. Fields shown\n2. Devices filtered",
                    "execution_type": "1",
                }
            ],
        }
        client = FakeWriteClient(testcase_readback=readback)
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_tool_preview", return_value=testcase_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_update_testcase(**testcase_args())["result"]
                        result = api.testlink_update_testcase(
                            **testcase_args(
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )
                        resumed = api.testlink_update_testcase(
                            **testcase_args(
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )
            self.assertTrue(result["ok"], result)
            audit = json.loads((Path(tmpdir) / result["result"]["audit_id"]).read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertEqual("verified", result["result"]["verification_status"])
        self.assertEqual(1, len(client.testcase_payloads))
        self.assertEqual("EMS-1", client.last_queries[-1]["testcase_external_id"])
        self.assertEqual("success", audit["status"])
        self.assertTrue(resumed["ok"])
        self.assertEqual("skipped-resume", resumed["result"]["status"])
        self.assertEqual(1, len(client.testcase_payloads))

    def test_changed_testcase_payload_invalidates_preview_digest(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient()
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch(
                    "testlink_mcp.api._legacy_tool_preview",
                    side_effect=[testcase_preview(summary="Before"), testcase_preview(summary="After")],
                ):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_update_testcase(
                            **testcase_args(summary="Before")
                        )["result"]
                        result = api.testlink_update_testcase(
                            **testcase_args(
                                summary="After",
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )

        self.assertFalse(result["ok"])
        self.assertEqual([], client.testcase_payloads)

    def test_create_testcase_write_also_verifies_readback(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        create_preview = {
            "mode": "preview",
            "target": {"project": {"id": "10", "name": "EMS"}, "suite": {"id": "55", "name": "Filter"}},
            "payload": {
                "testprojectid": "10",
                "testsuiteid": "55",
                "testcasename": "Filter case",
                "authorlogin": "alice",
                "summary": "",
                "steps": testcase_preview()["payload"]["steps"],
                "importance": 2,
                "executiontype": 1,
                "checkduplicatedname": 1,
                "actiononduplicatedname": "block",
            },
            "duplicate_found": False,
        }
        readback = {
            "testcaseid": "501",
            "name": "Filter case",
            "summary": "",
            "importance": "2",
            "execution_type": "1",
            "steps": [
                {
                    "actions": "1. Filter page\n2. Apply",
                    "expected_results": "1. Fields shown\n2. Devices filtered",
                    "execution_type": "1",
                }
            ],
        }
        client = FakeWriteClient(testcase_readback=readback)
        create_args = {
            "operation_id": "operation-create-case-1",
            "environment": "sandbox",
            "project": "EMS",
            "suite_id": "55",
            "name": "Filter case",
            "author_login": "alice",
            "steps": testcase_args()["steps"],
        }
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_tool_preview", return_value=create_preview):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_create_testcase(**create_args)["result"]
                        result = api.testlink_create_testcase(
                            **create_args,
                            write=True,
                            preview_digest=preview["preview_digest"],
                            audit_dir=tmpdir,
                        )

        self.assertTrue(result["ok"], result)
        self.assertEqual("verified", result["result"]["verification_status"])
        self.assertEqual("501", result["result"]["testcase_id"])
        self.assertEqual("create", client.testcase_payloads[0][0])

    def test_testcase_readback_mismatch_is_not_reported_as_success(self) -> None:
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient(
            testcase_readback={
                "testcaseexternalid": "EMS-1",
                "steps": [
                    {
                        "actions": "Wrong action",
                        "expected_results": "Wrong expected",
                        "execution_type": "1",
                    }
                ],
            }
        )
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_tool_preview", return_value=testcase_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_update_testcase(**testcase_args())["result"]
                        result = api.testlink_update_testcase(
                            **testcase_args(
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )
            audit_path = next(Path(tmpdir).glob("*.json"))
            audit = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        self.assertEqual("TESTCASE_READBACK_MISMATCH", result["error"]["error"]["code"])
        self.assertEqual("verification_failed", audit["status"])
        self.assertTrue(audit["write_succeeded"])

    def test_testcase_write_failure_audit_redacts_secret_and_is_indeterminate(self) -> None:
        os.environ["TESTLINK_DEVKEY"] = "testlink-secret"
        runtime = SimpleNamespace(environment="sandbox")
        client = FakeWriteClient(fail=True)
        with TemporaryDirectory() as tmpdir:
            with patch("testlink_mcp.api.load_runtime", return_value=runtime):
                with patch("testlink_mcp.api._legacy_tool_preview", return_value=testcase_preview()):
                    with patch("testlink_mcp.api.write_client", return_value=client):
                        preview = api.testlink_update_testcase(**testcase_args())["result"]
                        result = api.testlink_update_testcase(
                            **testcase_args(
                                write=True,
                                preview_digest=preview["preview_digest"],
                                audit_dir=tmpdir,
                            )
                        )
            audit_path = next(Path(tmpdir).glob("*.json"))
            audit_text = audit_path.read_text(encoding="utf-8")
            audit = json.loads(audit_text)

        self.assertFalse(result["ok"])
        self.assertEqual("indeterminate", audit["status"])
        self.assertNotIn("testlink-secret", audit_text)
        self.assertIn("*****", audit_text)


class CountingTestLinkClient:
    """Offline XML-RPC stand-in that counts authentication and name-resolution calls."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.payloads: list[dict] = []

    def _count(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def check_devkey(self) -> bool:
        self._count("check_devkey")
        return True

    def get_projects(self):
        self._count("get_projects")
        return [{"id": "10", "name": "EMS"}]

    def get_project_test_plans(self, project_id):
        self._count("get_project_test_plans")
        return [{"id": "20", "name": "Regression"}]

    def get_builds(self, plan_id):
        self._count("get_builds")
        return [{"id": "40", "name": "build-1"}]

    def get_platforms(self, plan_id):
        self._count("get_platforms")
        return [{"id": "30", "name": "Default Platform"}]

    def report_result(self, payload):
        self._count("report_result")
        self.payloads.append(payload)
        return [{"status": True, "operation": "reportTCResult", "id": 9000 + len(self.payloads)}]


class TestLinkMcpBatchSessionTests(unittest.TestCase):
    CASES = 6

    def setUp(self) -> None:
        api._batch_session.clear()
        self.runtime = SimpleNamespace(
            environment="sandbox",
            settings=TestLinkSettings(url="https://testlink.invalid", devkey="synthetic-devkey", timeout=60),
        )
        self.client = CountingTestLinkClient()

    def tearDown(self) -> None:
        api._batch_session.clear()

    def _run_batch(self, *, session: bool, audit_dir: str) -> list[dict]:
        env = {"TESTLINK_AGENT_PROFILE": "sandbox", api.BATCH_SESSION_ENV: "1" if session else ""}
        results = []
        with patch.dict(os.environ, env), \
                patch("testlink_mcp.api.load_runtime", return_value=self.runtime), \
                patch("testlink_mcp.api.write_client", return_value=self.client) as write_factory, \
                patch("testlink_agent_core.api._client", return_value=self.client) as legacy_factory:
            for index in range(self.CASES):
                case_args = args(
                    operation_id=f"operation-batch-{index}",
                    testcase_external_id=f"EMS-{index + 1}",
                    execution_duration=1.5,
                )
                preview = api.testlink_report_execution(**case_args)
                self.assertTrue(preview["ok"], preview)
                written = api.testlink_report_execution(
                    **case_args,
                    write=True,
                    preview_digest=preview["result"]["preview_digest"],
                    audit_dir=audit_dir,
                )
                self.assertTrue(written["ok"], written)
                results.append({"preview": preview["result"], "write": written["result"]})
        # Each factory call is one authenticated client, i.e. one tl.checkDevKey.
        self.authentications = write_factory.call_count + legacy_factory.call_count
        return results

    def test_batch_session_authenticates_and_resolves_the_target_once(self) -> None:
        with TemporaryDirectory() as tmpdir:
            results = self._run_batch(session=True, audit_dir=tmpdir)

        self.assertEqual(1, self.authentications)
        for name in ("get_projects", "get_project_test_plans", "get_builds", "get_platforms"):
            self.assertEqual(1, self.client.calls[name], name)
        self.assertEqual(self.CASES, self.client.calls["report_result"])
        self.assertEqual(
            [f"EMS-{index + 1}" for index in range(self.CASES)],
            [payload["testcaseexternalid"] for payload in self.client.payloads],
        )
        self.assertEqual({"success"}, {result["write"]["status"] for result in results})
        self.assertEqual({1.5}, {result["preview"]["execution"]["duration_minutes"] for result in results})

    def test_without_session_every_call_resolves_again(self) -> None:
        with TemporaryDirectory() as tmpdir:
            self._run_batch(session=False, audit_dir=tmpdir)

        # One preview and one write per testcase, each resolving again; the write
        # also authenticates a second client for reportTCResult.
        self.assertEqual(3 * self.CASES, self.authentications)
        self.assertEqual(2 * self.CASES, self.client.calls["get_projects"])
        self.assertEqual(2 * self.CASES, self.client.calls["get_builds"])
        self.assertEqual({}, api._batch_session)

    def test_session_preview_digest_matches_the_one_shot_path(self) -> None:
        with TemporaryDirectory() as first_dir, TemporaryDirectory() as second_dir:
            session_results = self._run_batch(session=True, audit_dir=first_dir)
            api._batch_session.clear()
            one_shot_results = self._run_batch(session=False, audit_dir=second_dir)

        self.assertEqual(
            [result["preview"]["preview_digest"] for result in one_shot_results],
            [result["preview"]["preview_digest"] for result in session_results],
        )

    def test_changed_credentials_do_not_reuse_the_session_connection(self) -> None:
        env = {"TESTLINK_AGENT_PROFILE": "sandbox", api.BATCH_SESSION_ENV: "1"}
        other_runtime = SimpleNamespace(
            environment="sandbox",
            settings=TestLinkSettings(url="https://testlink.invalid", devkey="other-devkey", timeout=60),
        )
        with patch.dict(os.environ, env), \
                patch("testlink_mcp.api.load_runtime", side_effect=[self.runtime, other_runtime]), \
                patch("testlink_mcp.api.write_client", return_value=self.client) as factory:
            first = api.testlink_report_execution(**args())
            second = api.testlink_report_execution(**args())

        self.assertTrue(first["ok"] and second["ok"])
        self.assertEqual(2, factory.call_count)
        self.assertEqual(2, self.client.calls["get_projects"])


if __name__ == "__main__":
    unittest.main()
