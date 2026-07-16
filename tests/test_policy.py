import os
import unittest

from testlink_agent_core.errors import TestLinkError
from testlink_agent_core.models import ParsedResult
from testlink_agent_core.policy import (
    blocked_manager_fields,
    build_dedupe_key,
    build_failure_signature,
    dedupe_digest,
    validate_environment_pair,
)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.saved_env = {
            key: os.environ.get(key)
            for key in (
                "TESTLINK_AGENT_PROFILE",
                "REDMINE_ENV",
                "REDMINE_ALLOW_MANAGER_FIELDS",
            )
        }
        for key in self.saved_env:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _result(self) -> ParsedResult:
        return ParsedResult(
            external_id="EMS1-7128",
            test_name="test_login",
            raw_status="Fail",
            status="f",
            duration_text="1s",
            duration_seconds=1.0,
        )

    def _context(self, build_name: str = "03.00.11(AAVV.221)b5") -> dict:
        return {
            "project": {"name": "EMS"},
            "plan": {"name": "Regression"},
            "platform": {"name": "NetAtlas EMS"},
            "build": {"name": build_name},
        }

    def test_failure_signature_normalizes_noise(self):
        result = self._result()

        signature = build_failure_signature(
            result,
            failure_summary=r"Timeout at C:\tmp\run-123\log.txt  0xABCDEF",
        )

        self.assertEqual(signature, "test_login|fail|timeout at <path> <hex>")

    def test_dedupe_key_is_stable_across_builds(self):
        result = self._result()

        left = build_dedupe_key(
            redmine_project_id="ems",
            context=self._context("build-1"),
            result=result,
            failure_summary="Result Fail",
        )
        right = build_dedupe_key(
            redmine_project_id="ems",
            context=self._context("build-2"),
            result=result,
            failure_summary="Result Fail",
        )

        self.assertEqual(left, right)
        self.assertIn("testcase_external_id=ems1-7128", left)
        self.assertIn("failure_signature=test_login|fail|result fail", left)

    def test_dedupe_digest_is_short_stable_hash(self):
        key = build_dedupe_key(
            redmine_project_id="ems",
            context=self._context(),
            result=self._result(),
            failure_summary="Result Fail",
        )

        self.assertEqual(dedupe_digest(key), dedupe_digest(key))
        self.assertEqual(len(dedupe_digest(key)), 16)

    def test_environment_pair_defaults_to_corp(self):
        self.assertEqual(validate_environment_pair(), ("corp", "corp"))

    def test_environment_pair_rejects_mixed_targets(self):
        os.environ["TESTLINK_AGENT_PROFILE"] = "corp"
        os.environ["REDMINE_ENV"] = "sandbox"

        with self.assertRaisesRegex(TestLinkError, "must match"):
            validate_environment_pair()

    def test_blocks_manager_fields_by_default(self):
        blocked = blocked_manager_fields({"assigned_to_id": "123", "fixed_version_id": "9"})

        self.assertEqual(blocked, ["assigned_to_id", "fixed_version_id"])

    def test_manager_switch_allows_manager_fields(self):
        os.environ["REDMINE_ALLOW_MANAGER_FIELDS"] = "true"

        self.assertEqual(blocked_manager_fields({"assigned_to_id": "123"}), [])


if __name__ == "__main__":
    unittest.main()
