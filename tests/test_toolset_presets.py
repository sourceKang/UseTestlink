from __future__ import annotations

import os
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: tomllib is 3.11+; only one test needs it.
    tomllib = None
from unittest.mock import patch

from qa_integration_agent.server import handle_request as qa_handle_request
from qa_integration_agent.tools import TOOLSETS as QA_TOOLSETS
from qa_integration_agent.tools import tools_for_toolset as qa_tools
from qa_mcp_contracts.release import PACKAGE_VERSION
from qa_mcp_contracts.toolsets import PRESET_NAMES, resolve_tool_names
from redmine_mcp.server import handle_request as redmine_handle_request
from redmine_mcp.tools import PRESETS as REDMINE_PRESETS
from redmine_mcp.tools import tools_for_toolset as redmine_tools
from testlink_agent_core import server as legacy_server
from testlink_agent_core.doctor import diagnose
from testlink_mcp.server import handle_request as testlink_handle_request
from testlink_mcp.tools import PRESETS as TESTLINK_PRESETS
from testlink_mcp.tools import TOOLS as TESTLINK_TOOLS
from testlink_mcp.tools import tools_for_toolset as testlink_tools


def names(tools) -> set[str]:
    return {tool["name"] for tool in tools}


def read_only(tools) -> bool:
    return all(tool.get("annotations", {}).get("readOnlyHint") for tool in tools)


class ResolveToolNamesTests(unittest.TestCase):
    TOOLSETS = {"a": {"x"}, "b": {"y"}, "c": {"x", "z"}}
    PRESETS = {"ab": ("a", "b")}

    def resolve(self, value):
        return resolve_tool_names(value, toolsets=self.TOOLSETS, presets=self.PRESETS, env_name="T")

    def test_comma_list_and_presets_are_unioned(self) -> None:
        self.assertEqual({"x", "y", "z"}, self.resolve(" A , c,b "))
        self.assertEqual({"x", "y"}, self.resolve("ab"))
        self.assertEqual({"x", "y", "z"}, self.resolve("ab,c"))

    def test_unknown_or_empty_names_fail_closed(self) -> None:
        for value in ("", "a,", ",a", "a,,b", "nope", "a,nope"):
            with self.subTest(value=value), self.assertRaises(ValueError) as raised:
                self.resolve(value)
            self.assertIn("T must name one or more of", str(raised.exception))


class TestLinkPresetTests(unittest.TestCase):
    def test_presets_cover_the_three_usage_profiles(self) -> None:
        self.assertEqual(set(PRESET_NAMES), set(TESTLINK_PRESETS))
        self.assertEqual(set(PRESET_NAMES), set(REDMINE_PRESETS))

    def test_batch_import_equals_execution_plus_maintenance(self) -> None:
        batch = names(testlink_tools("batch-import"))
        self.assertEqual(names(testlink_tools("execution,maintenance")), batch)
        for expected in (
            "testlink_create_build",
            "testlink_update_testcase",
            "testlink_create_testcase",
            "testlink_report_execution",
            "testlink_link_bug",
            "get_last_result",
        ):
            self.assertIn(expected, batch)
        for excluded in ("delete_execution", "add_case_to_plan", "upload_attachment", "testlink_delete_profile"):
            self.assertNotIn(excluded, batch)

    def test_manual_qa_is_execution_and_bug_tracking_is_read_only(self) -> None:
        self.assertEqual(names(testlink_tools("execution")), names(testlink_tools("manual-qa")))
        self.assertIn("testlink_create_build", names(testlink_tools("manual-qa")))
        tracking = testlink_tools("bug-tracking")
        self.assertTrue(tracking)
        self.assertTrue(read_only(tracking))

    def test_legacy_create_build_is_replaced_by_the_protected_tool(self) -> None:
        all_names = names(TESTLINK_TOOLS)
        self.assertNotIn("create_build", all_names)
        self.assertIn("testlink_create_build", all_names)

    def test_server_accepts_comma_list_and_rejects_tools_outside_it(self) -> None:
        with patch.dict(os.environ, {"TESTLINK_MCP_TOOLSET": "manual-qa,maintenance"}):
            listed = testlink_handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            blocked = testlink_handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "delete_execution", "arguments": {}},
                }
            )
        listed_names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("testlink_create_build", listed_names)
        self.assertIn("testlink_update_testcase", listed_names)
        self.assertEqual(-32602, blocked["error"]["code"])


class RedmineToolsetTests(unittest.TestCase):
    def test_unset_toolset_is_read_only_discovery(self) -> None:
        with patch.dict(os.environ, {}):
            os.environ.pop("REDMINE_MCP_TOOLSET", None)
            tools = redmine_tools()
            listed = redmine_handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            blocked = redmine_handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "redmine_create_bug", "arguments": {}},
                }
            )
        self.assertTrue(read_only(tools))
        self.assertEqual(
            {
                "redmine_search_issues",
                "redmine_get_issue",
                "redmine_list_projects",
                "redmine_health",
                "redmine_get_project_metadata",
                "redmine_validate_template",
            },
            names(tools),
        )
        self.assertEqual(names(tools), {tool["name"] for tool in listed["result"]["tools"]})
        self.assertEqual(-32602, blocked["error"]["code"])

    def test_presets(self) -> None:
        self.assertEqual(names(redmine_tools("issue")), names(redmine_tools("manual-qa")))
        for preset in ("batch-import", "bug-tracking"):
            with self.subTest(preset=preset):
                tools = redmine_tools(preset)
                self.assertTrue(read_only(tools))
                self.assertNotIn("redmine_create_bug", names(tools))
        self.assertEqual(
            names(redmine_tools("issue")) | names(redmine_tools("metadata")),
            names(redmine_tools("issue,metadata")),
        )


class QaToolsetTests(unittest.TestCase):
    def test_default_and_batch_import_preset_select_import(self) -> None:
        with patch.dict(os.environ, {}):
            os.environ.pop("QA_INTEGRATION_TOOLSET", None)
            default = names(qa_tools())
        self.assertEqual(QA_TOOLSETS["import"], default)
        self.assertEqual(default, names(qa_tools("batch-import")))

    def test_profiles_without_the_coordinator_are_rejected(self) -> None:
        for preset in ("manual-qa", "bug-tracking"):
            with self.subTest(preset=preset), self.assertRaises(ValueError):
                qa_tools(preset)


class DoctorToolsetTests(unittest.TestCase):
    def check(self, server: str, value: str) -> dict:
        key = "TESTLINK_MCP_TOOLSET" if server == "testlink" else "REDMINE_MCP_TOOLSET"
        with patch.dict(os.environ, {key: value}):
            result = diagnose(server=server)
        return next(check for check in result["checks"] if check["check"] == "toolset")

    def test_presets_pass_and_any_all_member_warns(self) -> None:
        self.assertEqual("ok", self.check("testlink", "batch-import")["status"])
        self.assertEqual("ok", self.check("redmine", "manual-qa")["status"])
        self.assertEqual("warning", self.check("testlink", "execution, ALL")["status"])
        self.assertEqual("error", self.check("redmine", "issue,unknown")["status"])


class PackageVersionTests(unittest.TestCase):
    def test_every_server_reports_the_package_version(self) -> None:
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        for handler in (
            testlink_handle_request,
            redmine_handle_request,
            qa_handle_request,
            legacy_server.handle_request,
        ):
            with self.subTest(handler=handler.__module__):
                response = handler(initialize)
                self.assertEqual(PACKAGE_VERSION, response["result"]["serverInfo"]["version"])

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_pyproject_reads_the_single_version_source(self) -> None:
        pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("version", pyproject["project"]["dynamic"])
        self.assertNotIn("version", pyproject["project"])
        self.assertEqual(
            "qa_mcp_contracts.release.PACKAGE_VERSION",
            pyproject["tool"]["setuptools"]["dynamic"]["version"]["attr"],
        )


if __name__ == "__main__":
    unittest.main()
