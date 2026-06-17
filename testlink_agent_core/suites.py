from __future__ import annotations

from typing import Any

from .clients import TestLinkClient
from .errors import TestLinkError


def normalize_suite(suite: dict[str, Any], path: str, depth: int) -> dict[str, Any]:
    return {
        "id": str(suite.get("id") or ""),
        "name": suite.get("name"),
        "parent_id": suite.get("parent_id"),
        "node_order": suite.get("node_order"),
        "path": path,
        "depth": depth,
        "raw": suite,
    }

def collect_test_suites(
    client: TestLinkClient,
    project_id: str,
    parent_suite_id: str | None = None,
    parent_path: str = "",
    depth: int = 0,
    recursive: bool = True,
) -> list[dict[str, Any]]:
    raw_suites = (
        client.get_child_test_suites(parent_suite_id)
        if parent_suite_id
        else client.get_first_level_test_suites(project_id)
    )
    suites: list[dict[str, Any]] = []
    for suite in raw_suites:
        suite_id = str(suite.get("id") or "")
        name = str(suite.get("name") or "")
        path = f"{parent_path}/{name}" if parent_path else name
        normalized = normalize_suite(suite, path, depth)
        suites.append(normalized)
        if recursive and suite_id:
            suites.extend(
                collect_test_suites(
                    client,
                    project_id,
                    parent_suite_id=suite_id,
                    parent_path=path,
                    depth=depth + 1,
                    recursive=True,
                )
            )
    return suites

def resolve_suite_by_name(suites: list[dict[str, Any]], suite_name: str) -> dict[str, Any]:
    target = suite_name.casefold()
    path_matches = [
        suite
        for suite in suites
        if str(suite.get("path") or "").casefold() == target
    ]
    if len(path_matches) == 1:
        return path_matches[0]
    if len(path_matches) > 1:
        choices = ", ".join(f"{suite.get('path')} (id={suite.get('id')})" for suite in path_matches[:10])
        raise TestLinkError(f"Multiple test suite paths match {suite_name}. Use --suite-id. Matches: {choices}")

    matches = [
        suite
        for suite in suites
        if str(suite.get("name") or "").casefold() == target
    ]
    if not matches:
        raise TestLinkError(f"Test suite not found by name/path: {suite_name}")
    if len(matches) > 1:
        choices = ", ".join(f"{suite.get('path')} (id={suite.get('id')})" for suite in matches[:10])
        raise TestLinkError(f"Multiple test suites match {suite_name}. Use --suite-id. Matches: {choices}")
    return matches[0]

def text_contains(value: Any, needle: str | None) -> bool:
    if not needle:
        return True
    return needle.casefold() in str(value or "").casefold()

def suite_search_row(project: dict[str, Any], suite: dict[str, Any]) -> dict[str, Any]:
    project_name = str(project.get("name") or "")
    suite_id = str(suite.get("id") or "")
    return {
        "project": project_name,
        "testprojectid": project.get("id"),
        "suite_id": suite_id,
        "suite_name": suite.get("name"),
        "suite_path": suite.get("path"),
        "create_args": [
            "--project",
            project_name,
            "--suite-id",
            suite_id,
        ],
        "create_example": (
            f'python .\\testlink_agent.py create-testcase --project "{project_name}" '
            f'--suite-id {suite_id} --author-login "your-testlink-login" --name "your_testcase_name" '
            f'--summary "your summary" --step "Action => Expected result"'
        ),
    }
