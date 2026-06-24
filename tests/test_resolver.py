import unittest

from testlink_agent_core.errors import TestLinkError
from testlink_agent_core.resolver import NameResolver


class FakeClient:
    def get_projects(self):
        return [
            {"id": "10", "name": "Gateway", "active": "1"},
            {"id": "11", "name": "EMS", "active": "1"},
        ]

    def get_project_test_plans(self, project_id):
        self.last_project_id = project_id
        return [
            {"id": "20", "name": "Regression", "testproject_id": project_id},
            {"id": "21", "name": "Smoke", "testproject_id": project_id},
        ]

    def get_platforms(self, testplan_id):
        return [{"id": "30", "name": "Windows"}]

    def get_builds(self, testplan_id):
        return [{"id": "40", "name": "1.2.3 build 4"}]


class ResolverTests(unittest.TestCase):
    def test_resolves_project_and_plan_by_exact_name(self):
        resolver = NameResolver(FakeClient())

        project = resolver.resolve_project("Gateway")
        plan = resolver.resolve_test_plan("Gateway", "Regression")

        self.assertEqual(project["id"], "10")
        self.assertEqual(plan["id"], "20")

    def test_project_not_found_returns_suggestions(self):
        resolver = NameResolver(FakeClient())

        with self.assertRaises(TestLinkError) as context:
            resolver.resolve_project("Gate")

        self.assertIn("Gateway", context.exception.to_dict()["raw"]["suggestions"])

    def test_plan_not_found_returns_suggestions(self):
        resolver = NameResolver(FakeClient())

        with self.assertRaises(TestLinkError) as context:
            resolver.resolve_test_plan("Gateway", "Regress")

        self.assertIn("Regression", context.exception.to_dict()["raw"]["suggestions"])


if __name__ == "__main__":
    unittest.main()
