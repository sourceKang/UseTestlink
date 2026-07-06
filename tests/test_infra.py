import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class InfraTests(unittest.TestCase):
    def test_redmine_sandbox_readme_is_explicitly_non_production(self):
        readme = (ROOT / "infra" / "redmine-sandbox" / "README.md").read_text(encoding="utf-8")

        self.assertIn("development-only Redmine sandbox", readme)
        self.assertIn("Do not use this sandbox as the corporate defect system", readme)
        self.assertIn("TESTLINK_AGENT_PROFILE=sandbox", readme)
        self.assertIn("REDMINE_ENV=sandbox", readme)

    def test_redmine_sandbox_compose_uses_sandbox_names(self):
        compose = (ROOT / "infra" / "redmine-sandbox" / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("redmine_sandbox", compose)
        self.assertNotIn("REDMINE_ENV=corp", compose)
        self.assertNotIn("TESTLINK_AGENT_PROFILE=corp", compose)


if __name__ == "__main__":
    unittest.main()
