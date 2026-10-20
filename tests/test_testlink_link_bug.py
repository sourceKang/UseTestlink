from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from testlink_agent_core.errors import TestLinkError
from testlink_mcp import api, custom_api
from testlink_mcp.server import handle_request
from testlink_mcp.tools import TOOLS, tools_for_toolset


XMLRPC_URL = "http://testlink.example/testlink/lib/api/xmlrpc/v1/xmlrpc.php"
ECHO_BODY = "testplan_id:20<br>build_id:40<br>tc_id:EMS-1<br>issue_id:259886<br>"
# Shape of a tl.getTestCasesForTestPlan(details="simple") row from TestLink 1.9 (field names
# confirmed read-only against the corporate server; values are fixtures).
PLAN_CASE_ROW = {
    "tcase_id": "50",
    "tcase_name": "Login",
    "tc_id": "50",
    "tcversion_id": "60",
    "tcversion_number": "1",
    "full_external_id": "EMS-1",
    "external_id": "1",
    "platform_id": "30",
    "feature_id": "70",
}


def runtime(environment: str = "sandbox"):
    return SimpleNamespace(
        environment=environment,
        settings=SimpleNamespace(url=XMLRPC_URL, timeout=60),
    )


def link_args(**overrides):
    values = {
        "operation_id": "operation-link-bug-1",
        "environment": "sandbox",
        "project": "EMS",
        "plan": "Regression",
        "platform": "Default Platform",
        "build": "build-1",
        "testcase_external_id": "EMS-1",
        "bug_ids": ["259886"],
        "status": "f",
        "notes": "Linked by automation",
    }
    values.update(overrides)
    return values


class FakeTargetClient:
    def __init__(self, *, cases=None, last_execution=None, readback_error: bool = False):
        self.cases = cases if cases is not None else {"EMS-1": dict(PLAN_CASE_ROW)}
        self.last_execution = last_execution
        self.readback_error = readback_error
        self.last_queries = []

    def get_projects(self):
        return [{"id": "10", "name": "EMS"}]

    def get_project_test_plans(self, project_id):
        return [{"id": "20", "name": "Regression"}]

    def get_platforms(self, testplan_id):
        return [{"id": "30", "name": "Default Platform"}]

    def get_builds(self, testplan_id):
        return [{"id": "40", "name": "build-1"}]

    def get_plan_cases_by_external_id(self, testplan_id, platform_id):
        return self.cases

    def get_last_execution_result(self, **kwargs):
        self.last_queries.append(kwargs)
        if self.readback_error:
            raise TestLinkError("readback failed")
        return self.last_execution


class TestLinkBugTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {"TESTLINK_TESTER_ID": "1001", "TESTLINK_BUG_LINK_ENDPOINT": "bug_link.php"})
        self.env.start()
        os.environ.pop(custom_api.WEB_BASE_URL_ENV, None)

    def tearDown(self) -> None:
        self.env.stop()

    def run_link(self, client, *, send=None, **overrides):
        with (
            patch("testlink_mcp.api.load_runtime", return_value=runtime()),
            patch("testlink_mcp.api.write_client", return_value=client),
            patch("testlink_mcp.api.custom_api.set_result", side_effect=send) as sent,
        ):
            result = api.call_tool("testlink_link_bug", link_args(**overrides))
        return result, sent

    def preview_digest(self, client, **overrides) -> str:
        result, sent = self.run_link(client, **overrides)
        self.assertTrue(result["ok"], result)
        sent.assert_not_called()
        return result["result"]["preview_digest"]

    def test_preview_is_default_and_never_calls_the_endpoint(self) -> None:
        result, sent = self.run_link(FakeTargetClient(), bug_ids=["259886", "259886", "260088"])

        self.assertTrue(result["ok"], result)
        sent.assert_not_called()
        preview = result["result"]
        self.assertEqual("preview", preview["mode"])
        self.assertTrue(preview["planned_write"])
        self.assertEqual(64, len(preview["preview_digest"]))
        self.assertEqual(["259886", "260088"], preview["link"]["bug_ids"])
        self.assertEqual("1001", preview["link"]["tester_id"])
        self.assertEqual("f", preview["link"]["status"])
        self.assertEqual("http://testlink.example/testlink/bug_link.php", preview["link"]["endpoint"])
        self.assertEqual(
            {"external_id": "EMS-1", "id": "50", "name": "Login", "version_id": "60"},
            preview["target"]["testcase"],
        )
        self.assertEqual("5", preview["link"]["execution_duration_minutes"])
        self.assertEqual(3, len(preview["side_effects"]))
        self.assertTrue(any("please double check the issue" in effect for effect in preview["side_effects"]))
        self.assertTrue(any("redmine_get_issue" in check for check in preview["pre_write_checks"]))
        self.assertEqual([], preview["warnings"])

    def test_generic_case_keys_remain_a_fallback(self) -> None:
        result, _ = self.run_link(FakeTargetClient(cases={"EMS-1": {"id": "50", "name": "Login"}}))

        self.assertTrue(result["ok"], result)
        testcase = result["result"]["target"]["testcase"]
        self.assertEqual(("50", "Login"), (testcase["id"], testcase["name"]))
        self.assertEqual([], result["result"]["warnings"])

    def test_missing_testcase_name_is_warned_in_preview(self) -> None:
        row = {key: value for key, value in PLAN_CASE_ROW.items() if key != "tcase_name"}
        result, sent = self.run_link(FakeTargetClient(cases={"EMS-1": row}))

        self.assertTrue(result["ok"], result)
        self.assertEqual("", result["result"]["target"]["testcase"]["name"])
        self.assertTrue(any("no testcase id/name" in warning for warning in result["result"]["warnings"]))
        sent.assert_not_called()

    def test_non_fail_status_is_warned_in_preview(self) -> None:
        result, _ = self.run_link(FakeTargetClient(), status="p")

        self.assertTrue(result["ok"], result)
        self.assertEqual(1, len(result["result"]["warnings"]))

    def test_tester_id_has_no_default(self) -> None:
        os.environ.pop("TESTLINK_TESTER_ID", None)
        result, sent = self.run_link(FakeTargetClient())

        self.assertFalse(result["ok"])
        self.assertEqual("CONFIG_MISSING", result["error"]["error"]["code"])
        sent.assert_not_called()

    def test_link_endpoint_has_no_default_and_must_be_a_script_name(self) -> None:
        os.environ.pop("TESTLINK_BUG_LINK_ENDPOINT", None)
        result, sent = self.run_link(FakeTargetClient())
        self.assertFalse(result["ok"])
        self.assertEqual("CONFIG_MISSING", result["error"]["error"]["code"])
        for value in ("../bug_link.php", "dir/bug_link.php", "bug_link.php?x=1", "http://other/bug_link.php", "bug_link"):
            with self.subTest(value=value):
                os.environ["TESTLINK_BUG_LINK_ENDPOINT"] = value
                result, sent = self.run_link(FakeTargetClient())
                self.assertFalse(result["ok"])
                self.assertEqual("CONFIG_INVALID", result["error"]["error"]["code"])
                sent.assert_not_called()

    def test_changing_the_endpoint_invalidates_the_preview_digest(self) -> None:
        client = FakeTargetClient()
        digest = self.preview_digest(client)
        os.environ["TESTLINK_BUG_LINK_ENDPOINT"] = "other_link.php"
        with TemporaryDirectory() as tmp:
            result, sent = self.run_link(client, write=True, preview_digest=digest, audit_dir=tmp)
        self.assertFalse(result["ok"])
        sent.assert_not_called()

    def test_non_numeric_tester_id_is_rejected(self) -> None:
        os.environ["TESTLINK_TESTER_ID"] = "Kang"
        result, _ = self.run_link(FakeTargetClient())

        self.assertFalse(result["ok"])
        self.assertEqual("CONFIG_INVALID", result["error"]["error"]["code"])

    def test_invalid_inputs_are_rejected_before_any_lookup(self) -> None:
        cases = [
            {"testcase_external_id": "EMS-1_2"},
            {"testcase_external_id": "EMS1"},
            {"bug_ids": ["#259886"]},
            {"bug_ids": ["0"]},
            {"bug_ids": ["0259886"]},
            {"bug_ids": []},
            {"bug_ids": "259886"},
            {"bug_ids": [str(index) for index in range(1, 22)]},
            {"status": "skip"},
            {"execution_duration": -1},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result, sent = self.run_link(FakeTargetClient(), **overrides)
                self.assertFalse(result["ok"])
                self.assertEqual("INVALID_ARGUMENT", result["error"]["error"]["code"])
                sent.assert_not_called()

    def test_prefix_with_underscore_is_accepted(self) -> None:
        result, _ = self.run_link(
            FakeTargetClient(cases={"EMS_X-12": {**PLAN_CASE_ROW, "tcase_id": "51", "tcase_name": "Logout"}}),
            testcase_external_id="EMS_X-12",
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual("EMS_X-12", result["result"]["link"]["testcase_external_id"])

    def test_non_boolean_write_stays_a_preview(self) -> None:
        client = FakeTargetClient()
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            for value in ("true", 1):
                with self.subTest(write=value):
                    result, sent = self.run_link(
                        client,
                        send=lambda base_url, endpoint, params, timeout: ECHO_BODY,
                        write=value,
                        preview_digest=digest,
                        audit_dir=tmp,
                    )
                    self.assertTrue(result["ok"], result)
                    self.assertEqual("preview", result["result"]["mode"])
                    sent.assert_not_called()
            self.assertEqual([], list(Path(tmp).glob("*.json")))

    def test_tester_id_is_read_from_the_mcp_env_file(self) -> None:
        os.environ.pop("TESTLINK_TESTER_ID", None)
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "testlink_mcp.env"
            env_file.write_text(
                "\n".join(
                    [
                        "TESTLINK_AGENT_PROFILE=sandbox",
                        f"TESTLINK_URL={XMLRPC_URL}",
                        "TESTLINK_DEVKEY=offline-devkey",
                        "TESTLINK_TESTER_ID=1001",
                        "TESTLINK_BUG_LINK_ENDPOINT=bug_link.php",
                    ]
                ),
                encoding="utf-8",
            )
            cleared = {
                key: ""
                for key in (
                    "TESTLINK_AGENT_PROFILE",
                    "TESTLINK_URL",
                    "TESTLINK_DEVKEY",
                    "TESTLINK_TESTER_ID",
                    "TESTLINK_BUG_LINK_ENDPOINT",
                )
            }
            with (
                patch.dict(os.environ, cleared),
                patch("testlink_mcp.api.write_client", return_value=FakeTargetClient()),
                patch("testlink_mcp.api.custom_api.set_result") as sent,
            ):
                result = api.call_tool("testlink_link_bug", link_args(env_file=str(env_file)))

        self.assertTrue(result["ok"], result)
        self.assertEqual("1001", result["result"]["link"]["tester_id"])
        self.assertNotIn("offline-devkey", json.dumps(result))
        sent.assert_not_called()

    def test_testcase_outside_plan_platform_is_rejected(self) -> None:
        result, sent = self.run_link(FakeTargetClient(cases={}))

        self.assertFalse(result["ok"])
        self.assertIn("not uniquely present", result["error"]["error"]["message"])
        sent.assert_not_called()

    def test_environment_mismatch_fails_before_lookup(self) -> None:
        with (
            patch("testlink_mcp.api.load_runtime", return_value=runtime("corp")),
            patch("testlink_mcp.api.write_client") as client_factory,
            patch("testlink_mcp.api.custom_api.set_result") as sent,
        ):
            result = api.call_tool("testlink_link_bug", link_args())

        self.assertFalse(result["ok"])
        client_factory.assert_not_called()
        sent.assert_not_called()

    def test_write_requires_matching_preview_digest(self) -> None:
        with TemporaryDirectory() as tmp:
            client = FakeTargetClient()
            for digest in (None, "0" * 64):
                with self.subTest(digest=digest):
                    result, sent = self.run_link(client, write=True, preview_digest=digest, audit_dir=tmp)
                    self.assertFalse(result["ok"])
                    sent.assert_not_called()
            audits = [json.loads(path.read_text(encoding="utf-8")) for path in Path(tmp).glob("*.json")]
            self.assertTrue(audits)
            self.assertTrue(all(audit["status"] == "failed" for audit in audits))

    def test_changed_bug_ids_invalidate_preview_digest(self) -> None:
        client = FakeTargetClient()
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            result, sent = self.run_link(
                client, bug_ids=["260088"], write=True, preview_digest=digest, audit_dir=tmp
            )

        self.assertFalse(result["ok"])
        sent.assert_not_called()

    def test_confirmed_write_submits_once_and_reports_link_unverified(self) -> None:
        client = FakeTargetClient(last_execution=[{"id": "3200001", "status": "f", "tester_id": "1001"}])
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            result, sent = self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: ECHO_BODY,
                write=True,
                preview_digest=digest,
                audit_dir=tmp,
            )
            audits = list(Path(tmp).glob("*.json"))
            audit = json.loads(audits[0].read_text(encoding="utf-8"))

        self.assertTrue(result["ok"], result)
        sent.assert_called_once()
        base_url, endpoint, params = sent.call_args.args
        self.assertEqual("bug_link.php", endpoint)
        self.assertEqual("http://testlink.example/testlink", base_url)
        self.assertEqual(
            {
                "testplan_id": "20",
                "build_id": "40",
                "platform_id": "30",
                "tester_id": "1001",
                "tc_id": "EMS-1",
                "status": "f",
                "issue_id": "259886",
                "notes": "Linked by automation",
                "execution_duration": "5",
            },
            params,
        )
        outcome = result["result"]
        self.assertEqual("submitted", outcome["status"])
        self.assertEqual("link_unverified", outcome["verification_status"])
        self.assertTrue(outcome["execution_readback"]["matches"])
        self.assertEqual("3200001", outcome["execution_readback"]["execution_id"])
        self.assertEqual(1, len(audits))
        self.assertEqual([], outcome["warnings"])
        self.assertEqual("submitted", audit["status"])
        self.assertEqual("link-bug", audit["action"])
        self.assertEqual(["259886"], audit["bug_ids"])

    def test_readback_mismatch_or_error_does_not_hide_the_submission(self) -> None:
        for client in (
            FakeTargetClient(last_execution=[{"id": "1", "status": "p", "tester_id": "49"}]),
            FakeTargetClient(readback_error=True),
        ):
            with self.subTest(client=client), TemporaryDirectory() as tmp:
                digest = self.preview_digest(client)
                result, _ = self.run_link(
                    client,
                    send=lambda base_url, endpoint, params, timeout: ECHO_BODY,
                    write=True,
                    preview_digest=digest,
                    audit_dir=tmp,
                )
                self.assertTrue(result["ok"], result)
                self.assertEqual("submitted", result["result"]["status"])
                self.assertFalse(result["result"]["execution_readback"].get("matches", False))
                self.assertEqual(1, len(result["result"]["warnings"]))

    def test_submitted_operation_is_skipped_on_retry(self) -> None:
        client = FakeTargetClient(last_execution=[{"id": "1", "status": "f", "tester_id": "1001"}])
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            first, _ = self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: ECHO_BODY,
                write=True,
                preview_digest=digest,
                audit_dir=tmp,
            )
            second, sent = self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: ECHO_BODY,
                write=True,
                preview_digest=digest,
                audit_dir=tmp,
            )

        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual("skipped-resume", second["result"]["status"])
        sent.assert_not_called()

    def test_unechoed_response_is_indeterminate_and_blocks_retry(self) -> None:
        client = FakeTargetClient()
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            first, _ = self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: "<html>Fatal error</html>",
                write=True,
                preview_digest=digest,
                audit_dir=tmp,
            )
            audit = json.loads(next(Path(tmp).glob("*.json")).read_text(encoding="utf-8"))
            retry, sent = self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: ECHO_BODY,
                write=True,
                preview_digest=digest,
                audit_dir=tmp,
            )

        self.assertFalse(first["ok"])
        self.assertEqual("LINK_BUG_NOT_ECHOED", first["error"]["error"]["code"])
        self.assertEqual("indeterminate", audit["status"])
        self.assertFalse(retry["ok"])
        self.assertEqual("LINK_BUG_INDETERMINATE", retry["error"]["error"]["code"])
        sent.assert_not_called()

    def test_indeterminate_operation_blocks_a_changed_payload(self) -> None:
        client = FakeTargetClient()
        digest = self.preview_digest(client)
        changed_digest = self.preview_digest(client, notes="Changed notes")
        self.assertNotEqual(digest, changed_digest)
        with TemporaryDirectory() as tmp:
            self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: "<html>Fatal error</html>",
                write=True,
                preview_digest=digest,
                audit_dir=tmp,
            )
            retry, sent = self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: ECHO_BODY,
                notes="Changed notes",
                write=True,
                preview_digest=changed_digest,
                audit_dir=tmp,
            )

        self.assertFalse(retry["ok"])
        self.assertEqual("LINK_BUG_INDETERMINATE", retry["error"]["error"]["code"])
        sent.assert_not_called()

    def test_submitted_operation_id_cannot_be_reused_for_another_link(self) -> None:
        client = FakeTargetClient(last_execution=[{"id": "1", "status": "f", "tester_id": "1001"}])
        digest = self.preview_digest(client)
        other_digest = self.preview_digest(client, bug_ids=["260088"])
        with TemporaryDirectory() as tmp:
            first, _ = self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: ECHO_BODY,
                write=True,
                preview_digest=digest,
                audit_dir=tmp,
            )
            second, sent = self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: ECHO_BODY,
                bug_ids=["260088"],
                write=True,
                preview_digest=other_digest,
                audit_dir=tmp,
            )

        self.assertTrue(first["ok"], first)
        self.assertFalse(second["ok"])
        self.assertEqual("LINK_BUG_OPERATION_REUSED", second["error"]["error"]["code"])
        sent.assert_not_called()

    def test_http_error_is_recorded_as_indeterminate(self) -> None:
        client = FakeTargetClient()
        digest = self.preview_digest(client)

        def fail(base_url, endpoint, params, timeout):
            raise TestLinkError("Bug-link endpoint request failed: timed out", code="HTTP_ERROR")

        with TemporaryDirectory() as tmp:
            result, _ = self.run_link(client, send=fail, write=True, preview_digest=digest, audit_dir=tmp)
            audit = json.loads(next(Path(tmp).glob("*.json")).read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        self.assertEqual("indeterminate", audit["status"])

    def test_audit_redacts_secrets_echoed_by_the_endpoint(self) -> None:
        client = FakeTargetClient(last_execution=[{"id": "1", "status": "f", "tester_id": "1001"}])
        digest = self.preview_digest(client)
        with TemporaryDirectory() as tmp:
            self.run_link(
                client,
                send=lambda base_url, endpoint, params, timeout: ECHO_BODY + "TESTLINK_DEVKEY=testlink-secret<br>",
                write=True,
                preview_digest=digest,
                audit_dir=tmp,
            )
            text = next(Path(tmp).glob("*.json")).read_text(encoding="utf-8")

        self.assertNotIn("testlink-secret", text)


class CustomApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = os.environ.pop(custom_api.WEB_BASE_URL_ENV, None)

    def tearDown(self) -> None:
        os.environ.pop(custom_api.WEB_BASE_URL_ENV, None)
        if self.saved is not None:
            os.environ[custom_api.WEB_BASE_URL_ENV] = self.saved

    def test_web_base_url_is_derived_from_testlink_url(self) -> None:
        self.assertEqual("http://testlink.example/testlink", custom_api.web_base_url(XMLRPC_URL))
        self.assertEqual(
            "http://testlink.example/testlink",
            custom_api.web_base_url("http://testlink.example/testlink/index.php?caller=login"),
        )
        self.assertEqual("https://tl.example/testlink", custom_api.web_base_url("https://tl.example/testlink/"))
        self.assertEqual("http://tl.example/testlink", custom_api.web_base_url("http://tl.example/testlink/xmlrpc.php"))

    def test_web_base_url_override_must_stay_on_the_same_host(self) -> None:
        os.environ[custom_api.WEB_BASE_URL_ENV] = "http://other.example/testlink"
        with self.assertRaises(TestLinkError):
            custom_api.web_base_url(XMLRPC_URL)
        os.environ[custom_api.WEB_BASE_URL_ENV] = "http://testlink.example/tl/"
        self.assertEqual("http://testlink.example/tl", custom_api.web_base_url(XMLRPC_URL))

    def test_set_result_encodes_params_and_uses_the_proxyless_opener(self) -> None:
        opened = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return ECHO_BODY.encode("utf-8")

        def fake_open(url, timeout):
            opened.append((url, timeout))
            return FakeResponse()

        with patch.object(custom_api._opener, "open", side_effect=fake_open):
            body = custom_api.set_result(
                "http://testlink.example/testlink/",
                "bug_link.php",
                {"tc_id": "EMS-1", "issue_id": "1,2", "notes": "a b&c"},
                timeout=7,
            )

        self.assertEqual(ECHO_BODY, body)
        self.assertEqual(
            "http://testlink.example/testlink/bug_link.php?tc_id=EMS-1&issue_id=1%2C2&notes=a+b%26c",
            opened[0][0],
        )
        self.assertEqual(7, opened[0][1])
        self.assertTrue(custom_api.response_reached(body))
        self.assertFalse(custom_api.response_reached("<html></html>"))


class LinkBugToolSurfaceTests(unittest.TestCase):
    def test_tool_defaults_to_preview_and_requires_explicit_target(self) -> None:
        tool = next(tool for tool in TOOLS if tool["name"] == "testlink_link_bug")
        schema = tool["inputSchema"]
        self.assertFalse(schema["properties"]["write"]["default"])
        self.assertNotIn("tester_id", schema["properties"])
        for field in ("operation_id", "environment", "project", "plan", "platform", "build",
                      "testcase_external_id", "bug_ids", "status"):
            self.assertIn(field, schema["required"])
        self.assertFalse(tool["annotations"]["readOnlyHint"])

    def test_tool_is_in_execution_toolset_only_among_task_toolsets(self) -> None:
        self.assertIn("testlink_link_bug", {tool["name"] for tool in tools_for_toolset("execution")})
        for toolset in ("integration", "maintenance", "discovery"):
            self.assertNotIn("testlink_link_bug", {tool["name"] for tool in tools_for_toolset(toolset)})

    def test_server_lists_tool_in_execution_toolset(self) -> None:
        with patch.dict(os.environ, {"TESTLINK_MCP_TOOLSET": "execution"}):
            listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertIn("testlink_link_bug", {tool["name"] for tool in listed["result"]["tools"]})

    def test_unknown_argument_is_reported_not_raised(self) -> None:
        result = api.call_tool("testlink_link_bug", {"operation_id": "operation-link-bug-1", "tester_id": "49"})
        self.assertFalse(result["ok"])
        self.assertEqual("arguments", result["error"]["error"]["stage"])


if __name__ == "__main__":
    unittest.main()
