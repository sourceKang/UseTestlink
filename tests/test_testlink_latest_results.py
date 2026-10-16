from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from testlink_agent_core.errors import TestLinkError
from testlink_mcp import api
from testlink_mcp.tools import TOOLS, tools_for_toolset


def latest_args(**overrides):
    values = {
        "operation_id": "latest-results-1",
        "environment": "sandbox",
        "project": "NetAtlasEMS",
        "plan": "NetAtlas EMS",
        "platform": "NetAtlas EMS",
        "build": "03.00.11 (AAVV.221) b13_redhat",
    }
    values.update(overrides)
    return values


class FakeResultsClient:
    def __init__(self, results=None, failing=()):
        self.results = results or {
            "EMS1-10": [{"id": "301", "status": "p", "tester_id": "37", "execution_ts": "2026-10-14 15:41:00"}],
            "EMS1-2": [{"id": "302", "status": "f", "tester_id": "37", "execution_ts": "2026-10-14 15:41:01"}],
            "EMS1-3": [{"id": -1}],
        }
        self.failing = set(failing)
        self.queries = []

    def get_projects(self):
        return [{"id": "67654", "name": "NetAtlasEMS"}]

    def get_project_test_plans(self, project_id):
        return [{"id": "260150", "name": "NetAtlas EMS"}]

    def get_platforms(self, testplan_id):
        return [{"id": "49", "name": "NetAtlas EMS"}]

    def get_builds(self, testplan_id):
        return [{"id": "20644", "name": "03.00.11 (AAVV.221) b13_redhat"}]

    def get_plan_cases_by_external_id(self, testplan_id, platform_id):
        return {key: {"id": key} for key in self.results}

    def get_last_execution_result(self, **kwargs):
        self.queries.append(kwargs)
        external_id = kwargs["testcase_external_id"]
        if external_id in self.failing:
            raise TestLinkError("read failed with TESTLINK_DEVKEY=testlink-secret")
        return self.results[external_id]


class LatestResultsTests(unittest.TestCase):
    def run_tool(self, client, **overrides):
        with (
            patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="sandbox")),
            patch("testlink_mcp.api.write_client", return_value=client),
        ):
            return api.call_tool("testlink_get_latest_results", latest_args(**overrides))

    def test_listed_testcases_are_read_on_the_exact_build_and_platform(self) -> None:
        client = FakeResultsClient()
        result = self.run_tool(client, testcase_external_ids=["EMS1-10", "ems1-2", "EMS1-3", "EMS1-10"])

        self.assertTrue(result["ok"], result)
        outcome = result["result"]
        self.assertEqual("read-only", outcome["mode"])
        self.assertEqual(3, outcome["requested"])
        self.assertEqual({"f": 1, "n": 1, "p": 1}, outcome["status_counts"])
        self.assertEqual(["EMS1-10", "EMS1-2", "EMS1-3"], [row["testcase_external_id"] for row in outcome["rows"]])
        self.assertEqual({"status": "n", "execution_id": None}, {
            key: outcome["rows"][2][key] for key in ("status", "execution_id")
        })
        self.assertEqual("37", outcome["rows"][0]["tester_id"])
        self.assertEqual(
            {"testplan_id": "260150", "testcase_external_id": "EMS1-2", "build_id": "20644", "platform_id": "49"},
            client.queries[1],
        )

    def test_expected_status_lists_only_mismatches(self) -> None:
        result = self.run_tool(FakeResultsClient(), all_in_plan=True, expected_status="p")

        self.assertTrue(result["ok"], result)
        outcome = result["result"]
        self.assertEqual(3, outcome["requested"])
        self.assertEqual(["EMS1-2", "EMS1-3"], [row["testcase_external_id"] for row in outcome["rows"]])
        self.assertEqual(1, outcome["rows_omitted"])
        self.assertFalse(outcome["all_match_expected"])

    def test_all_in_plan_is_sorted_numerically_and_can_fully_match(self) -> None:
        client = FakeResultsClient(
            results={
                "EMS1-10": [{"id": "1", "status": "p"}],
                "EMS1-9": [{"id": "2", "status": "p"}],
            }
        )
        result = self.run_tool(client, all_in_plan=True, expected_status="p")

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["result"]["all_match_expected"])
        self.assertEqual([], result["result"]["rows"])
        self.assertEqual(["EMS1-9", "EMS1-10"], [query["testcase_external_id"] for query in client.queries])

    def test_unknown_testcases_and_read_errors_are_reported_per_item(self) -> None:
        client = FakeResultsClient(failing={"EMS1-2"})
        result = self.run_tool(client, testcase_external_ids=["EMS1-10", "EMS1-2", "EMS1-999"], expected_status="p")

        self.assertTrue(result["ok"], result)
        outcome = result["result"]
        self.assertEqual(["EMS1-999"], outcome["not_in_plan"])
        self.assertEqual("EMS1-2", outcome["errors"][0]["testcase_external_id"])
        self.assertNotIn("testlink-secret", str(result))
        self.assertFalse(outcome["all_match_expected"])
        self.assertEqual(2, len(client.queries))

    def test_invalid_arguments_fail_before_any_lookup(self) -> None:
        cases = [
            {},
            {"testcase_external_ids": ["EMS1-1"], "all_in_plan": True},
            {"testcase_external_ids": []},
            {"testcase_external_ids": ["EMS1-1_2"]},
            {"testcase_external_ids": "EMS1-1"},
            {"testcase_external_ids": [f"EMS1-{index}" for index in range(1, 1003)]},
            {"all_in_plan": "yes"},
            {"all_in_plan": True, "expected_status": "u"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with (
                    patch("testlink_mcp.api.load_runtime", return_value=SimpleNamespace(environment="sandbox")),
                    patch("testlink_mcp.api.write_client") as client_factory,
                ):
                    result = api.call_tool("testlink_get_latest_results", latest_args(**overrides))
                self.assertFalse(result["ok"])
                self.assertEqual("INVALID_ARGUMENT", result["error"]["error"]["code"])
                client_factory.assert_not_called()

    def test_inexact_target_fails_closed(self) -> None:
        result = self.run_tool(FakeResultsClient(), build="b13", all_in_plan=True)
        self.assertFalse(result["ok"])
        self.assertIn("found 0", result["error"]["error"]["message"])

    def test_tool_is_read_only_and_in_reading_profiles(self) -> None:
        tool = next(tool for tool in TOOLS if tool["name"] == "testlink_get_latest_results")
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertNotIn("write", tool["inputSchema"]["properties"])
        for toolset in ("execution", "manual-qa", "batch-import", "bug-tracking", "discovery"):
            with self.subTest(toolset=toolset):
                self.assertIn("testlink_get_latest_results", {item["name"] for item in tools_for_toolset(toolset)})


if __name__ == "__main__":
    unittest.main()
