from __future__ import annotations

import urllib.request
import xmlrpc.client
from typing import Any

from .config import DEFAULT_TIMEOUT_SECONDS, normalize_endpoint
from .errors import TestLinkError
from .testcases import flatten_plan_cases


def xmlrpc_items_to_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if "id" in value and "name" in value:
            return [value]
        return [item for item in value.values() if isinstance(item, dict)]
    return []

class TestLinkClient:
    def __init__(self, base_url: str, devkey: str, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.endpoint = normalize_endpoint(base_url)
        self.devkey = devkey
        self.timeout = timeout

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        body = xmlrpc.client.dumps(
            () if params is None else (params,),
            methodname=method,
            allow_none=True,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "text/xml"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = response.read().lstrip()
        values, _method = xmlrpc.client.loads(payload)
        return values[0] if values else None

    def check_devkey(self) -> bool:
        return self.call("tl.checkDevKey", {"devKey": self.devkey}) is True

    def get_project_by_name(self, name: str) -> dict[str, Any]:
        project = self.call(
            "tl.getTestProjectByName",
            {"devKey": self.devkey, "testprojectname": name},
        )
        if not isinstance(project, dict) or "id" not in project:
            raise TestLinkError(f"Project not found or unexpected response: {name}")
        return project

    def get_projects(self) -> list[dict[str, Any]]:
        projects = self.call("tl.getProjects", {"devKey": self.devkey})
        return xmlrpc_items_to_list(projects)

    def get_test_plan_by_name(self, project_name: str, plan_name: str) -> dict[str, Any]:
        plans = self.call(
            "tl.getTestPlanByName",
            {
                "devKey": self.devkey,
                "testprojectname": project_name,
                "testplanname": plan_name,
            },
        )
        if isinstance(plans, list) and plans:
            return plans[0]
        if isinstance(plans, dict) and "id" in plans:
            return plans
        raise TestLinkError(f"Test Plan not found: {project_name} / {plan_name}")

    def get_platform_by_name(self, testplan_id: str, platform_name: str) -> dict[str, Any]:
        platforms = self.get_platforms(testplan_id)
        for platform in platforms:
            if str(platform.get("name", "")).casefold() == platform_name.casefold():
                return platform
        available = ", ".join(str(p.get("name")) for p in platforms)
        raise TestLinkError(f"Platform not found: {platform_name}. Available: {available}")

    def get_platforms(self, testplan_id: str) -> list[dict[str, Any]]:
        platforms = self.call(
            "tl.getTestPlanPlatforms",
            {"devKey": self.devkey, "testplanid": testplan_id},
        )
        return list(platforms or [])

    def get_project_test_plans(self, project_id: str) -> list[dict[str, Any]]:
        plans = self.call(
            "tl.getProjectTestPlans",
            {"devKey": self.devkey, "testprojectid": project_id},
        )
        return list(plans or [])

    def get_builds(self, testplan_id: str) -> list[dict[str, Any]]:
        builds = self.call(
            "tl.getBuildsForTestPlan",
            {"devKey": self.devkey, "testplanid": testplan_id},
        )
        return list(builds or [])

    def get_build(self, testplan_id: str, build: str | None, build_id: str | None) -> dict[str, Any]:
        builds = self.get_builds(testplan_id)
        if not build and not build_id:
            selected = choose_latest_open_build(builds or [])
            if selected:
                return selected
            raise TestLinkError("No active/open build found. Specify --build or --build-id.")

        for candidate in builds or []:
            if build_id and str(candidate.get("id")) == str(build_id):
                return candidate
            if build and str(candidate.get("name")) == build:
                return candidate
        target = f"id={build_id}" if build_id else f"name={build}"
        raise TestLinkError(f"Build not found: {target}")

    def get_plan_cases_by_external_id(self, testplan_id: str, platform_id: str) -> dict[str, dict[str, Any]]:
        raw_cases = self.get_plan_cases(testplan_id, platform_id, details="simple")
        cases: dict[str, dict[str, Any]] = {}
        for case in flatten_plan_cases(raw_cases):
            external_id = str(
                case.get("full_external_id")
                or case.get("external_id")
                or case.get("tc_external_id")
                or ""
            )
            if external_id and external_id != "None":
                cases[external_id] = case
        return cases

    def get_plan_cases(self, testplan_id: str, platform_id: str, details: str = "simple") -> Any:
        return self.call(
            "tl.getTestCasesForTestPlan",
            {
                "devKey": self.devkey,
                "testplanid": testplan_id,
                "platformid": platform_id,
                "details": details,
            },
        )

    def get_first_level_test_suites(self, project_id: str) -> list[dict[str, Any]]:
        suites = self.call(
            "tl.getFirstLevelTestSuitesForTestProject",
            {"devKey": self.devkey, "testprojectid": project_id},
        )
        return xmlrpc_items_to_list(suites)

    def get_child_test_suites(self, suite_id: str) -> list[dict[str, Any]]:
        suites = self.call(
            "tl.getTestSuitesForTestSuite",
            {"devKey": self.devkey, "testsuiteid": suite_id},
        )
        return xmlrpc_items_to_list(suites)

    def report_result(self, params: dict[str, Any]) -> Any:
        payload = {"devKey": self.devkey, **params}
        return self.call("tl.reportTCResult", payload)

    def create_test_case(self, params: dict[str, Any]) -> Any:
        payload = {"devKey": self.devkey, **params}
        return self.call("tl.createTestCase", payload)

    def update_test_case(self, params: dict[str, Any]) -> Any:
        payload = {"devKey": self.devkey, **params}
        return self.call("tl.updateTestCase", payload)
