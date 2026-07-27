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

    def test_common_instructions_stay_within_budget(self) -> None:
        instructions = build_token_budget_report(ROOT)["always_loaded_instructions"]
        self.assertLessEqual(instructions["AGENTS.md"]["characters"], 2500)
        self.assertLessEqual(instructions["testlink-agent/SKILL.md"]["characters"], 1800)


if __name__ == "__main__":
    unittest.main()
