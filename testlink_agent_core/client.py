from __future__ import annotations

import time
import urllib.error
import urllib.request
import xmlrpc.client
from typing import Any

from .config import DEFAULT_TIMEOUT_SECONDS, normalize_endpoint
from .errors import TestLinkError, mask_secrets, normalize_testlink_error
from .reports import choose_latest_open_build
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
    def __init__(
        self,
        base_url: str,
        devkey: str,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        *,
        max_retries: int = 0,
        min_interval_seconds: float = 0.03,
    ):
        self.endpoint = normalize_endpoint(base_url)
        self.devkey = devkey
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._last_call_at = 0.0

    def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

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

        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                self._wait_for_rate_limit()
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = response.read().lstrip()
                self._last_call_at = time.monotonic()
                values, _method = xmlrpc.client.loads(payload)
                return values[0] if values else None
            except xmlrpc.client.Fault:
                raise
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(0.2 * (attempt + 1))
        raise TestLinkError(f"TestLink XML-RPC call failed: {method}: {mask_secrets(last_error)}")

    def call_tl(self, method: str, params: dict[str, Any] | None = None) -> Any:
        payload = {"devKey": self.devkey}
        if params:
            payload.update(params)
        try:
            return self.call(method, payload)
        except Exception as exc:
            raise normalize_testlink_error(exc) from exc

    def check_devkey(self) -> bool:
        return self.call_tl("tl.checkDevKey") is True

    def about(self) -> Any:
        return self.call_tl("tl.about")

    def get_project_by_name(self, name: str) -> dict[str, Any]:
        project = self.call_tl("tl.getTestProjectByName", {"testprojectname": name})
        if not isinstance(project, dict) or "id" not in project:
            raise TestLinkError(f"Project not found or unexpected response: {name}")
        return project

    def get_projects(self) -> list[dict[str, Any]]:
        projects = self.call_tl("tl.getProjects")
        return xmlrpc_items_to_list(projects)

    def get_test_plan_by_name(self, project_name: str, plan_name: str) -> dict[str, Any]:
        plans = self.call_tl(
            "tl.getTestPlanByName",
            {
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
        platforms = self.call_tl("tl.getTestPlanPlatforms", {"testplanid": testplan_id})
        return list(platforms or [])

    def get_project_test_plans(self, project_id: str) -> list[dict[str, Any]]:
        plans = self.call_tl("tl.getProjectTestPlans", {"testprojectid": project_id})
        return list(plans or [])

    def get_builds(self, testplan_id: str) -> list[dict[str, Any]]:
        builds = self.call_tl("tl.getBuildsForTestPlan", {"testplanid": testplan_id})
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

    def get_plan_cases(self, testplan_id: str, platform_id: str | None = None, details: str = "simple") -> Any:
        payload: dict[str, Any] = {
            "testplanid": testplan_id,
            "details": details,
        }
        if platform_id:
            payload["platformid"] = platform_id
        return self.call_tl("tl.getTestCasesForTestPlan", payload)

    def get_suite_cases(self, testsuite_id: str, *, deep: bool = True, details: str = "full") -> list[dict[str, Any]]:
        cases = self.call_tl(
            "tl.getTestCasesForTestSuite",
            {
                "testsuiteid": testsuite_id,
                "deep": deep,
                "details": details,
            },
        )
        return xmlrpc_items_to_list(cases)

    def get_test_case(
        self,
        *,
        testcase_id: str | None = None,
        testcase_external_id: str | None = None,
        version: str | int | None = None,
    ) -> Any:
        payload: dict[str, Any] = {}
        if testcase_id:
            payload["testcaseid"] = testcase_id
        elif testcase_external_id:
            payload["testcaseexternalid"] = testcase_external_id
        else:
            raise TestLinkError("testcase_id or testcase_external_id is required.")
        if version not in (None, ""):
            payload["version"] = int(version) if str(version).isdigit() else version
        return self.call_tl("tl.getTestCase", payload)

    def get_last_execution_result(
        self,
        *,
        testplan_id: str,
        testcase_id: str | None = None,
        testcase_external_id: str | None = None,
        build_id: str | None = None,
        platform_id: str | None = None,
        platform_name: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"testplanid": testplan_id}
        if testcase_id:
            payload["testcaseid"] = testcase_id
        elif testcase_external_id:
            payload["testcaseexternalid"] = testcase_external_id
        else:
            raise TestLinkError("testcase_id or testcase_external_id is required.")
        if build_id:
            payload["buildid"] = build_id
        if platform_id:
            payload["platformid"] = platform_id
        if platform_name:
            payload["platformname"] = platform_name
        return self.call_tl("tl.getLastExecutionResult", payload)

    def get_first_level_test_suites(self, project_id: str) -> list[dict[str, Any]]:
        suites = self.call_tl("tl.getFirstLevelTestSuitesForTestProject", {"testprojectid": project_id})
        return xmlrpc_items_to_list(suites)

    def get_child_test_suites(self, suite_id: str) -> list[dict[str, Any]]:
        suites = self.call_tl("tl.getTestSuitesForTestSuite", {"testsuiteid": suite_id})
        return xmlrpc_items_to_list(suites)

    def report_result(self, params: dict[str, Any]) -> Any:
        return self.call_tl("tl.reportTCResult", params)

    def create_build(self, params: dict[str, Any]) -> Any:
        return self.call_tl("tl.createBuild", params)

    def create_test_case(self, params: dict[str, Any]) -> Any:
        return self.call_tl("tl.createTestCase", params)

    def update_test_case(self, params: dict[str, Any]) -> Any:
        return self.call_tl("tl.updateTestCase", params)

    def add_test_case_to_plan(self, params: dict[str, Any]) -> Any:
        return self.call_tl("tl.addTestCaseToTestPlan", params)

    def upload_test_case_attachment(self, params: dict[str, Any]) -> Any:
        return self.call_tl("tl.uploadTestCaseAttachment", params)

    def upload_test_suite_attachment(self, params: dict[str, Any]) -> Any:
        return self.call_tl("tl.uploadTestSuiteAttachment", params)

    def upload_test_project_attachment(self, params: dict[str, Any]) -> Any:
        return self.call_tl("tl.uploadTestProjectAttachment", params)

    def upload_execution_attachment(self, params: dict[str, Any]) -> Any:
        return self.call_tl("tl.uploadExecutionAttachment", params)
