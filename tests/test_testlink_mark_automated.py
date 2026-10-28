from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from testlink_mcp import api
from testlink_mcp.tools import tools_for_toolset


def mark_args(**overrides):
    values = {
        "operation_id": "operation-mark-automated-1",
        "environment": "sandbox",
        "project": "DemoProject",
        "plan": "Demo Plan",
        "platform": "Demo Platform",
        "testcase_external_ids": ["DEMO-1", "DEMO-2", "DEMO-3"],
    }
    values.update(overrides)
    return values


class FakeMarkClient:
    """Plan rows as tl.getTestCasesForTestPlan returns them: the linked version and its execution type."""

    def __init__(self, *, types=None, reject=(), ignore=()):
        self.types = dict(types or {"DEMO-1": "1", "DEMO-2": "2", "DEMO-3": "1"})
        self.reject = set(reject)
        self.ignore = set(ignore)
        self.calls = []

    def get_projects(self):
        return [{"id": "100", "name": "DemoProject"}]

    def get_project_test_plans(self, project_id):
        return [{"id": "200", "name": "Demo Plan"}]

    def get_platforms(self, testplan_id):
        return [{"id": "300", "name": "Demo Platform"}]

    def get_plan_cases_by_external_id(self, testplan_id, platform_id):
        return {
            external_id: {
                "tcase_id": str(1000 + index),
                "tcversion_id": str(2000 + index),
                "version": "3" if external_id == "DEMO-3" else "1",
                "execution_type": execution_type,
                "full_external_id": external_id,
            }
            for index, (external_id, execution_type) in enumerate(sorted(self.types.items()))
        }

    def set_test_case_execution_type(self, params):
        self.calls.append(params)
        external_id = params["testcaseexternalid"]
        if external_id in self.reject:
            return [{"code": 7000, "message": "Version does not exist"}]
        if external_id not in self.ignore:
            self.types[external_id] = str(params["executiontype"])
        # TestLink echoes the request arguments, devKey included.
        return [{**params, "devKey": "secret-devkey-value"}, None]


def audits(directory: str) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(Path(directory).glob("*.json"))]


class TestLinkMarkAutomatedTests(unittest.TestCase):
    def run_tool(self, client, **overrides):
        with (
            patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="sandbox")),
            patch("testlink_mcp.api.write_client", return_value=client),
        ):
            return api.call_tool("testlink_mark_automated", mark_args(**overrides))

    def preview(self, client, **overrides) -> dict:
        result = self.run_tool(client, **overrides)
        self.assertTrue(result["ok"], result)
        return result["result"]

    def test_preview_lists_only_manual_testcases_and_writes_nothing(self) -> None:
        client = FakeMarkClient()
        preview = self.preview(client)

        self.assertEqual([], client.calls)
        self.assertEqual("preview", preview["mode"])
        self.assertTrue(preview["planned_write"])
        self.assertEqual(64, len(preview["preview_digest"]))
        self.assertEqual("300", preview["target"]["platform"]["id"])
        self.assertEqual(3, preview["requested"])
        self.assertEqual(1, preview["already_automated"])
        self.assertEqual(
            [
                {"testcase_external_id": "DEMO-1", "version": "1", "current": "manual"},
                {"testcase_external_id": "DEMO-3", "version": "3", "current": "manual"},
            ],
            preview["to_mark"],
        )

    def test_all_automated_needs_no_write(self) -> None:
        client = FakeMarkClient(types={"DEMO-1": "2", "DEMO-2": "2", "DEMO-3": "2"})
        preview = self.preview(client)
        self.assertFalse(preview["planned_write"])
        self.assertEqual([], preview["to_mark"])

        with TemporaryDirectory() as tmp:
            result = self.run_tool(client, write=True, preview_digest=preview["preview_digest"], audit_dir=tmp)
        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["result"]["marked"])
        self.assertEqual([], client.calls)

    def test_testcase_outside_the_plan_fails_closed(self) -> None:
        client = FakeMarkClient()
        result = self.run_tool(client, testcase_external_ids=["DEMO-1", "DEMO-9"])
        self.assertFalse(result["ok"])
        self.assertEqual("NOT_IN_PLAN", result["error"]["error"]["code"])
        self.assertIn("DEMO-9", result["error"]["error"]["message"])

    def test_invalid_ids_are_rejected_before_lookup(self) -> None:
        for ids in ([], ["DEMO 1"], ["123"], "DEMO-1"):
            with self.subTest(ids=ids):
                with (
                    patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="sandbox")),
                    patch("testlink_mcp.api.write_client") as client_factory,
                ):
                    result = api.call_tool("testlink_mark_automated", mark_args(testcase_external_ids=ids))
                self.assertFalse(result["ok"])
                self.assertEqual("INVALID_ARGUMENT", result["error"]["error"]["code"])
                client_factory.assert_not_called()

    def test_write_requires_matching_digest(self) -> None:
        client = FakeMarkClient()
        digest = self.preview(client)["preview_digest"]
        with TemporaryDirectory() as tmp:
            for supplied, overrides in (
                (None, {}),
                ("0" * 64, {}),
                (digest, {"testcase_external_ids": ["DEMO-1"]}),
            ):
                with self.subTest(supplied=supplied, overrides=overrides):
                    result = self.run_tool(client, write=True, preview_digest=supplied, audit_dir=tmp, **overrides)
                    self.assertFalse(result["ok"])
            recorded = audits(tmp)
        self.assertEqual([], client.calls)
        self.assertTrue(recorded)
        self.assertTrue(all(record["status"] == "failed" for record in recorded))

    def test_confirmed_write_marks_only_manual_and_verifies_readback(self) -> None:
        client = FakeMarkClient()
        digest = self.preview(client)["preview_digest"]
        with TemporaryDirectory() as tmp:
            result = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
            recorded = audits(tmp)
            audit_text = "".join(path.read_text(encoding="utf-8") for path in Path(tmp).glob("*.json"))

        self.assertTrue(result["ok"], result)
        self.assertEqual("verified", result["result"]["verification_status"])
        self.assertEqual(["DEMO-1", "DEMO-3"], result["result"]["marked"])
        self.assertEqual(1, result["result"]["already_automated"])
        self.assertEqual(
            [
                {"testprojectid": "100", "testcaseexternalid": "DEMO-1", "version": 1, "executiontype": 2},
                {"testprojectid": "100", "testcaseexternalid": "DEMO-3", "version": 3, "executiontype": 2},
            ],
            client.calls,
        )
        self.assertEqual(["success"], [record["status"] for record in recorded])
        self.assertEqual("mark-automated", recorded[0]["action"])
        self.assertNotIn("secret-devkey-value", audit_text)

    def test_rejection_is_reported_and_a_retry_finishes_the_rest(self) -> None:
        client = FakeMarkClient(reject={"DEMO-3"})
        digest = self.preview(client)["preview_digest"]
        with TemporaryDirectory() as tmp:
            first = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
            self.assertFalse(first["ok"])
            self.assertEqual("MARK_AUTOMATED_INCOMPLETE", first["error"]["error"]["code"])
            self.assertEqual("1", client.types["DEMO-3"])
            self.assertEqual("2", client.types["DEMO-1"])

            client.reject.clear()
            client.calls.clear()
            second = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
            recorded = audits(tmp)

        self.assertTrue(second["ok"], second)
        self.assertEqual(["DEMO-3"], second["result"]["marked"])
        self.assertEqual(["DEMO-3"], [call["testcaseexternalid"] for call in client.calls])
        self.assertEqual(["success", "verification_failed"], sorted(record["status"] for record in recorded))

    def test_write_answered_but_not_applied_fails_verification(self) -> None:
        client = FakeMarkClient(ignore={"DEMO-1"})
        digest = self.preview(client)["preview_digest"]
        with TemporaryDirectory() as tmp:
            result = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
        self.assertFalse(result["ok"])
        self.assertEqual("MARK_AUTOMATED_INCOMPLETE", result["error"]["error"]["code"])

    def test_retry_after_success_skips_without_calls(self) -> None:
        client = FakeMarkClient()
        digest = self.preview(client)["preview_digest"]
        with TemporaryDirectory() as tmp:
            self.assertTrue(self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)["ok"])
            client.calls.clear()
            again = self.run_tool(client, write=True, preview_digest=digest, audit_dir=tmp)
        self.assertTrue(again["ok"], again)
        self.assertEqual("skipped-resume", again["result"]["status"])
        self.assertEqual(["DEMO-1", "DEMO-3"], again["result"]["marked"])
        self.assertEqual([], client.calls)

    def test_environment_mismatch_fails_before_lookup(self) -> None:
        with (
            patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="corp")),
            patch("testlink_mcp.api.write_client") as client_factory,
        ):
            result = api.call_tool("testlink_mark_automated", mark_args())
        self.assertFalse(result["ok"])
        client_factory.assert_not_called()

    def test_tool_is_offered_with_testcase_maintenance_only(self) -> None:
        def names(toolset):
            return {tool["name"] for tool in tools_for_toolset(toolset)}

        self.assertIn("testlink_mark_automated", names("maintenance"))
        self.assertIn("testlink_mark_automated", names("batch-import"))
        for toolset in ("manual-qa", "bug-tracking", "discovery", "lookup"):
            self.assertNotIn("testlink_mark_automated", names(toolset))


if __name__ == "__main__":
    unittest.main()
