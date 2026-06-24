from __future__ import annotations

from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any

from .client import TestLinkClient
from .errors import TestLinkError
from .suites import collect_test_suites


@dataclass
class ResolverCache:
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    plans_by_project: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    suites_by_project: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    platforms_by_plan: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    builds_by_plan: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class NameResolver:
    def __init__(self, client: TestLinkClient, cache: ResolverCache | None = None):
        self.client = client
        self.cache = cache or ResolverCache()

    def resolve_project(self, name: str) -> dict[str, Any]:
        key = normalize_key(name)
        if key not in self.cache.projects:
            projects = self.client.get_projects()
            self.cache.projects = {
                normalize_key(project.get("name")): project
                for project in projects
                if project.get("name")
            }
        project = self.cache.projects.get(key)
        if project:
            return project
        raise TestLinkError(
            f"Test project not found: {name}",
            raw={"suggestions": _suggest(name, [project.get("name") for project in self.cache.projects.values()])},
            possible_causes=["名稱不完全相符，請確認 TestLink 顯示名稱"],
        )

    def resolve_test_plan(self, project_name: str, plan_name: str) -> dict[str, Any]:
        project = self.resolve_project(project_name)
        project_id = str(project["id"])
        if project_id not in self.cache.plans_by_project:
            self.cache.plans_by_project[project_id] = self.client.get_project_test_plans(project_id)
        plans = self.cache.plans_by_project[project_id]
        matches = [plan for plan in plans if normalize_key(plan.get("name")) == normalize_key(plan_name)]
        if len(matches) == 1:
            return matches[0]
        raise TestLinkError(
            f"Test plan not found: {project_name} / {plan_name}",
            raw={"suggestions": _suggest(plan_name, [plan.get("name") for plan in plans])},
            possible_causes=["名稱不完全相符，請確認 TestLink 顯示名稱"],
        )

    def resolve_suite(self, project_name: str, suite_name: str) -> dict[str, Any]:
        project = self.resolve_project(project_name)
        suites = self.get_suites(project)
        target = normalize_key(suite_name)
        path_matches = [suite for suite in suites if normalize_key(suite.get("path")) == target]
        if len(path_matches) == 1:
            return path_matches[0]
        name_matches = [suite for suite in suites if normalize_key(suite.get("name")) == target]
        if len(name_matches) == 1:
            return name_matches[0]
        if len(path_matches) > 1 or len(name_matches) > 1:
            matches = path_matches or name_matches
            raise TestLinkError(
                f"Multiple test suites match: {suite_name}",
                raw={"matches": _suite_choices(matches)},
                possible_causes=["suite 名稱重複，請改用完整 path 或 suite_id"],
            )
        choices = [str(suite.get("path") or suite.get("name") or "") for suite in suites]
        raise TestLinkError(
            f"Test suite not found: {project_name} / {suite_name}",
            raw={"suggestions": _suggest(suite_name, choices)},
            possible_causes=["名稱不完全相符，請確認 TestLink 顯示名稱"],
        )

    def resolve_platform(self, testplan_id: str, platform_name: str) -> dict[str, Any]:
        platforms = self.get_platforms(testplan_id)
        matches = [platform for platform in platforms if normalize_key(platform.get("name")) == normalize_key(platform_name)]
        if len(matches) == 1:
            return matches[0]
        raise TestLinkError(
            f"Platform not found: {platform_name}",
            raw={"suggestions": _suggest(platform_name, [platform.get("name") for platform in platforms])},
            possible_causes=["名稱不完全相符，或 test plan 未綁定此 platform"],
        )

    def resolve_build(self, testplan_id: str, build_name: str) -> dict[str, Any]:
        builds = self.get_builds(testplan_id)
        matches = [build for build in builds if normalize_key(build.get("name")) == normalize_key(build_name)]
        if len(matches) == 1:
            return matches[0]
        raise TestLinkError(
            f"Build not found: {build_name}",
            raw={"suggestions": _suggest(build_name, [build.get("name") for build in builds])},
            possible_causes=["名稱不完全相符，或 build 不屬於指定 test plan"],
        )

    def get_suites(self, project: dict[str, Any], recursive: bool = True) -> list[dict[str, Any]]:
        project_id = str(project["id"])
        if project_id not in self.cache.suites_by_project:
            self.cache.suites_by_project[project_id] = collect_test_suites(self.client, project_id, recursive=recursive)
        return self.cache.suites_by_project[project_id]

    def get_platforms(self, testplan_id: str) -> list[dict[str, Any]]:
        if testplan_id not in self.cache.platforms_by_plan:
            self.cache.platforms_by_plan[testplan_id] = self.client.get_platforms(testplan_id)
        return self.cache.platforms_by_plan[testplan_id]

    def get_builds(self, testplan_id: str) -> list[dict[str, Any]]:
        if testplan_id not in self.cache.builds_by_plan:
            self.cache.builds_by_plan[testplan_id] = self.client.get_builds(testplan_id)
        return self.cache.builds_by_plan[testplan_id]


def normalize_key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _suggest(needle: str, choices: list[Any]) -> list[str]:
    names = [str(choice) for choice in choices if choice not in (None, "")]
    normalized = {normalize_key(name): name for name in names}
    close_keys = get_close_matches(normalize_key(needle), list(normalized), n=5, cutoff=0.4)
    close = [normalized[key] for key in close_keys]
    partial = [
        name
        for name in names
        if normalize_key(needle) in normalize_key(name) or normalize_key(name) in normalize_key(needle)
    ]
    suggestions: list[str] = []
    for name in [*close, *partial]:
        if name not in suggestions:
            suggestions.append(name)
    return suggestions[:5]


def _suite_choices(suites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": suite.get("id"),
            "name": suite.get("name"),
            "path": suite.get("path"),
        }
        for suite in suites[:10]
    ]
