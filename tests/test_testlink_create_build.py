from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from testlink_agent_core.errors import TestLinkError
from testlink_mcp import api
from testlink_mcp.tools import TOOLS


def build_args(**overrides):
    values = {
        "operation_id": "operation-create-build-1",
        "environment": "sandbox",
        "project": "NetAtlasEMS",
        "plan": "NetAtlas EMS",
        "name": "03.00.11 (AAVV.221) b14_redhat",
        "notes": "RedHat b14 regression",
        "release_date": "2026-10-16",
    }
    values.update(overrides)
    return values


class FakeBuildClient:
    def __init__(self, *, builds=None, response=None, create_error=None, create_adds_build=True):
        self.builds = list(builds or [{"id": "20644", "name": "03.00.11 (AAVV.221) b13_redhat"}])
        self.response = response
        self.create_error = create_error
        self.create_adds_build = create_adds_build
        self.created = []

    def get_projects(self):
        return [{"id": "67654", "name": "NetAtlasEMS"}]

    def get_project_test_plans(self, project_id):
        return [{"id": "260150", "name": "NetAtlas EMS"}]

    def get_builds(self, testplan_id):
        return list(self.builds)

    def create_build(self, payload):
        self.created.append(payload)
        if self.create_error is not None:
            raise self.create_error
        if self.create_adds_build:
            self.builds.append({"id": "20700", "name": payload["buildname"]})
        if self.response is not None:
            return self.response
        return [{"status": True, "operation": "createBuild", "message": "Success!", "id": "20700"}]


def audits(directory: str) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in Path(directory).glob("*.json")]


class TestLinkCreateBuildTests(unittest.TestCase):
    def run_tool(self, client, **overrides):
        with (
            patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="sandbox")),
            patch("testlink_mcp.api.write_client", return_value=client),
        ):
            return api.call_tool("testlink_create_build", build_args(**overrides))

    def preview_digest(self, client, **overrides) -> str:
        result = self.run_tool(client, **overrides)
        self.assertTrue(result["ok"], result)
        return result["result"]["preview_digest"]

    def test_preview_is_default_and_creates_nothing(self) -> None:
        client = FakeBuildClient()
        result = self.run_tool(client)

        self.assertTrue(result["ok"], result)
        self.assertEqual([], client.created)
        preview = result["result"]
        self.assertEqual("preview", preview["mode"])
        self.assertTrue(preview["planned_write"])
        self.assertFalse(preview["duplicate_found"])
        self.assertEqual(64, len(preview["preview_digest"]))
        self.assertEqual("260150", preview["target"]["plan"]["id"])
        self.assertEqual(
            {
                "name": "03.00.11 (AAVV.221) b14_redhat",
                "notes": "RedHat b14 regression",
                "release_date": "2026-10-16",
                "active": True,
                "open": True,
            },
            preview["build"],
        )

    def test_existing_name_is_reported_and_blocks_write(self) -> None:
        client = FakeBuildClient(builds=[{"id": "20644", "name": "03.00.11  (AAVV.221) B14_redhat "}])
        preview = self.run_tool(client)
        self.assertTrue(preview["ok"], preview)
        self.assertFalse(preview["result"]["planned_write"])
        self.assertTrue(preview["result"]["duplicate_found"])
        self.assertEqual("20644", preview["result"]["duplicates"][0]["id"])

        with TemporaryDirectory() as tmp:
            result = self.run_tool(
                client, write=True, preview_digest=preview["result"]["preview_digest"], audit_dir=tmp
            )
            recorded = audits(tmp)

        self.assertFalse(result["ok"])
        self.assertEqual("BUILD_EXISTS", result["error"]["error"]["code"])
        self.assertEqual([], client.created)
        self.assertEqual(["failed"], [record["status"] for record in recorded])

    def test_invalid_inputs_are_rejected_before_lookup(self) -> None:
        cases = [
            {"name": ""},
            {"name": "   "},
            {"name": "b14 "},
            {"name": " b14"},
            {"release_date": "2026-13-01"},
            {"release_date": "2026/10/16"},
            {"release_date": "16-10-2026"},
            {"active": "yes"},
            {"open": 1},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with (
                    patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="sandbox")),
                    patch("testlink_mcp.api.write_client") as client_factory,
                ):
                    result = api.call_tool("testlink_create_build", build_args(**overrides))
                self.assertFalse(result["ok"])
                self.assertEqual("INVALID_ARGUMENT", result["error"]["error"]["code"])
                client_factory.assert_not_called()

    def test_environment_mismatch_fails_before_lookup(self) -> None:
        with (
            patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="corp")),
            patch("testlink_mcp.api.write_client") as client_factory,
        ):
            result = api.call_tool("testlink_create_build", build_args())
        self.assertFalse(result["ok"])
        client_factory.assert_not_called()

    def test_write_requires_matching_digest(self) -> None:
        client = FakeBuildClient()
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            for supplied, overrides in ((None, {}), ("0" * 64, {}), (digest, {"notes": "changed"})):
                with self.subTest(supplied=supplied, overrides=overrides):
                    result = self.run_tool(client, write=True, preview_digest=supplied, audit_dir=tmp, **overrides)
                    self.assertFalse(result["ok"])
            recorded = audits(tmp)
        self.assertEqual([], client.created)
        self.assertTrue(recorded)
        self.assertTrue(all(record["status"] == "failed" for record in recorded))

    def test_confirmed_write_creates_once_and_verifies_readback(self) -> None:
        client = FakeBuildClient()
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            result = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
            recorded = audits(tmp)

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [
                {
                    "testplanid": "260150",
                    "buildname": "03.00.11 (AAVV.221) b14_redhat",
                    "buildnotes": "RedHat b14 regression",
                    "active": 1,
                    "open": 1,
                    "releasedate": "2026-10-16",
                }
            ],
            client.created,
        )
        self.assertEqual("success", result["result"]["status"])
        self.assertEqual("verified", result["result"]["verification_status"])
        self.assertEqual({"id": "20700", "name": "03.00.11 (AAVV.221) b14_redhat"}, result["result"]["build"])
        self.assertEqual(1, len(recorded))
        self.assertEqual("success", recorded[0]["status"])
        self.assertEqual("create-build", recorded[0]["action"])

    def test_optional_release_date_and_flags_are_passed_as_given(self) -> None:
        client = FakeBuildClient()
        digest = self.preview_digest(client, release_date=None, active=False, open=False)
        with TemporaryDirectory() as tmp:
            result = self.run_tool(
                client,
                release_date=None,
                active=False,
                open=False,
                write=True,
                preview_digest=digest,
                audit_dir=tmp,
            )
        self.assertTrue(result["ok"], result)
        self.assertNotIn("releasedate", client.created[0])
        self.assertEqual((0, 0), (client.created[0]["active"], client.created[0]["open"]))

    def test_successful_operation_is_skipped_on_retry(self) -> None:
        client = FakeBuildClient()
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            first = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
            second = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual("skipped-resume", second["result"]["status"])
        self.assertEqual(1, len(client.created))

    def test_error_struct_is_a_definite_rejection(self) -> None:
        client = FakeBuildClient(
            response=[{"code": 7001, "message": "Build name already exists"}], create_adds_build=False
        )
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            result = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
            recorded = audits(tmp)

        self.assertFalse(result["ok"])
        self.assertEqual("BUILD_REJECTED", result["error"]["error"]["code"])
        self.assertEqual(["failed"], [record["status"] for record in recorded])

    def test_response_without_id_is_decided_by_readback(self) -> None:
        client = FakeBuildClient(response={"status": True})
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            result = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
        self.assertTrue(result["ok"], result)
        self.assertEqual("20700", result["result"]["build"]["id"])

    def test_missing_readback_fails_and_retry_recovers_once_build_appears(self) -> None:
        client = FakeBuildClient(create_adds_build=False)
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            first = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
            self.assertEqual(["verification_failed"], [record["status"] for record in audits(tmp)])
            still_missing = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
            client.builds.append({"id": "20701", "name": "03.00.11 (AAVV.221) b14_redhat"})
            recovered = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)

        self.assertFalse(first["ok"])
        self.assertEqual("BUILD_READBACK_MISMATCH", first["error"]["error"]["code"])
        self.assertFalse(still_missing["ok"])
        self.assertEqual("BUILD_WRITE_INDETERMINATE", still_missing["error"]["error"]["code"])
        self.assertTrue(recovered["ok"], recovered)
        self.assertEqual("skipped-resume", recovered["result"]["status"])
        self.assertEqual("20701", recovered["result"]["build"]["id"])
        self.assertEqual(1, len(client.created))

    def test_exception_during_create_is_indeterminate_and_redacted(self) -> None:
        client = FakeBuildClient(
            create_error=TestLinkError("timeout with TESTLINK_DEVKEY=testlink-secret"), create_adds_build=False
        )
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            result = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
            recorded = audits(tmp)
            text = "".join(path.read_text(encoding="utf-8") for path in Path(tmp).glob("*.json"))
            retry = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)

        self.assertFalse(result["ok"])
        self.assertEqual(["indeterminate"], [record["status"] for record in recorded])
        self.assertNotIn("testlink-secret", text)
        self.assertNotIn("testlink-secret", json.dumps(result))
        self.assertFalse(retry["ok"])
        self.assertEqual("BUILD_WRITE_INDETERMINATE", retry["error"]["error"]["code"])
        self.assertEqual(1, len(client.created))


class CreateBuildToolSurfaceTests(unittest.TestCase):
    def test_tool_defaults_to_preview_and_requires_identity(self) -> None:
        tool = next(tool for tool in TOOLS if tool["name"] == "testlink_create_build")
        schema = tool["inputSchema"]
        self.assertFalse(schema["properties"]["write"]["default"])
        self.assertFalse(schema["additionalProperties"])
        for field in ("operation_id", "environment", "project", "plan", "name"):
            self.assertIn(field, schema["required"])
        self.assertFalse(tool["annotations"]["readOnlyHint"])

    def test_unknown_argument_is_reported_not_raised(self) -> None:
        result = api.call_tool("testlink_create_build", {"operation_id": "operation-create-build-1", "foo": 1})
        self.assertFalse(result["ok"])
        self.assertEqual("arguments", result["error"]["error"]["stage"])


if __name__ == "__main__":
    unittest.main()
