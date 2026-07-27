from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from qa_integration_agent.tools import tools_for_toolset as qa_tools
from redmine_mcp.tools import tools_for_toolset as redmine_tools
from testlink_mcp.tools import tools_for_toolset as testlink_tools


def schema_characters(tools: list[dict[str, Any]]) -> int:
    return len(json.dumps(tools, ensure_ascii=False, separators=(",", ":")))


def estimated_tokens(characters: int) -> int:
    """Return a stable planning estimate; actual tokenizer counts vary by client."""
    return math.ceil(characters / 4)


def _metric(characters: int) -> dict[str, int]:
    return {"characters": characters, "estimated_tokens": estimated_tokens(characters)}


def build_token_budget_report(repository_root: str | Path) -> dict[str, Any]:
    root = Path(repository_root)
    all_schema_chars = sum(
        (
            schema_characters(testlink_tools("all")),
            schema_characters(redmine_tools("all")),
            schema_characters(qa_tools("all")),
        )
    )
    scenarios = {
        "legacy_all_servers": all_schema_chars,
        "qa_report_import": schema_characters(qa_tools("import")),
        "testlink_execution": schema_characters(testlink_tools("execution")),
        "testlink_testcase_maintenance": schema_characters(testlink_tools("maintenance")),
        "redmine_issue": schema_characters(redmine_tools("issue")),
    }
    return {
        "estimation": "UTF-8 text characters divided by four; use for regression comparison only.",
        "tool_schemas": {
            name: {
                **_metric(characters),
                "reduction_percent_vs_all": round((1 - characters / all_schema_chars) * 100, 1),
            }
            for name, characters in scenarios.items()
        },
        "always_loaded_instructions": {
            "AGENTS.md": _metric(len((root / "AGENTS.md").read_text(encoding="utf-8"))),
            "testlink-agent/SKILL.md": _metric(
                len((root / ".agents/skills/testlink-agent/SKILL.md").read_text(encoding="utf-8"))
            ),
        },
    }
