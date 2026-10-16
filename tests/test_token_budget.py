from __future__ import annotations

import unittest
from pathlib import Path

from testlink_agent_core.token_budget import build_token_budget_report


ROOT = Path(__file__).resolve().parent.parent


class TokenBudgetTests(unittest.TestCase):
    def test_task_profiles_reduce_persistent_tool_schema_by_at_least_70_percent(self) -> None:
        schemas = build_token_budget_report(ROOT)["tool_schemas"]
        for scenario in (
            "qa_report_import",
            "testlink_execution",
            "testlink_testcase_maintenance",
            "redmine_issue",
        ):
            self.assertGreaterEqual(schemas[scenario]["reduction_percent_vs_all"], 70.0, scenario)

    def test_profile_schemas_stay_under_regression_ceilings(self) -> None:
        # Ceilings sit a few percent above the 2026-10-16 measurements; raising one needs a stated reason.
        ceilings = {
            "qa_report_import": 1550,
            "testlink_execution": 2650,
            "testlink_testcase_maintenance": 2100,
            "redmine_issue": 2600,
            "redmine_discovery": 1300,
            "preset_batch_import_testlink": 4100,
            "preset_manual_qa_testlink": 2650,
            "preset_bug_tracking_testlink": 1550,
        }
        schemas = build_token_budget_report(ROOT)["tool_schemas"]
        for scenario, ceiling in ceilings.items():
            with self.subTest(scenario=scenario):
                self.assertLessEqual(schemas[scenario]["estimated_tokens"], ceiling)

    def test_lookup_is_the_read_only_side_of_execution(self) -> None:
        from testlink_mcp.tools import TOOLSETS, tools_for_toolset

        self.assertLessEqual(TOOLSETS["lookup"], TOOLSETS["execution"])
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in tools_for_toolset("lookup")))

    def test_common_instructions_stay_within_budget(self) -> None:
        instructions = build_token_budget_report(ROOT)["always_loaded_instructions"]
        self.assertLessEqual(instructions["AGENTS.md"]["characters"], 2500)
        self.assertLessEqual(instructions["testlink-agent/SKILL.md"]["characters"], 1800)


if __name__ == "__main__":
    unittest.main()
