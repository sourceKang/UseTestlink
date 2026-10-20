"""Every repo skill follows the layout the router and Codex expect."""

import re
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / ".agents" / "skills"
ROUTER = "testlink-agent"


def skill_dirs():
    return sorted(p for p in SKILLS.iterdir() if p.is_dir())


class SkillsLayoutTests(unittest.TestCase):
    def test_each_skill_has_skill_md_with_matching_name(self):
        for skill in skill_dirs():
            with self.subTest(skill=skill.name):
                skill_md = skill / "SKILL.md"
                self.assertTrue(skill_md.is_file(), "missing SKILL.md")
                text = skill_md.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"), "missing YAML frontmatter")
                name = re.search(r"^name:\s*(\S+)\s*$", text, re.MULTILINE)
                self.assertIsNotNone(name, "frontmatter has no name")
                self.assertEqual(skill.name, name.group(1))
                self.assertRegex(text, r"(?m)^description:\s*\S", "frontmatter has no description")

    def test_each_skill_has_codex_agent_config(self):
        for skill in skill_dirs():
            with self.subTest(skill=skill.name):
                self.assertTrue((skill / "agents" / "openai.yaml").is_file(), "missing agents/openai.yaml")

    def test_router_lists_every_skill(self):
        router = (SKILLS / ROUTER / "SKILL.md").read_text(encoding="utf-8")
        for skill in skill_dirs():
            if skill.name == ROUTER:
                continue
            with self.subTest(skill=skill.name):
                self.assertTrue(f"`{skill.name}`" in router, f"router {ROUTER}/SKILL.md does not list `{skill.name}`")


if __name__ == "__main__":
    unittest.main()
