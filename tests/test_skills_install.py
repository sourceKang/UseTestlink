from __future__ import annotations

import contextlib
import io
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from qa_mcp_contracts.release import PACKAGE_VERSION
from testlink_agent_core.cli import main
from testlink_agent_core.skills_install import MARKER, REPO_ONLY_SKILLS, install_skills

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: tomllib is 3.11+; only the packaging test needs it.
    tomllib = None


ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".agents" / "skills"
DOCS = ROOT / "docs"


def repo_skills() -> set[str]:
    return {path.name for path in SKILLS.iterdir() if (path / "SKILL.md").is_file()}


class InstallSkillsTests(unittest.TestCase):
    def install(self, target: Path, **kwargs):
        return install_skills(target=target, source=SKILLS, docs=DOCS, **kwargs)

    def test_installs_every_skill_except_repo_only_ones_with_their_docs(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp)
            result = self.install(target)

            self.assertTrue(result["ok"])
            installed = {item["skill"] for item in result["skills"]}
            self.assertEqual(repo_skills() - REPO_ONLY_SKILLS, installed)
            self.assertEqual({"install"}, {item["action"] for item in result["skills"]})
            for name in installed:
                with self.subTest(skill=name):
                    skill_dir = target / name
                    marker = json.loads((skill_dir / MARKER).read_text(encoding="utf-8"))
                    self.assertEqual(PACKAGE_VERSION, marker["version"])
                    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                    self.assertNotRegex(text, r"(?<![/\w])docs/[\w.-]+\.md")
                    for reference in re.findall(r"`([^`]*/docs/[\w.-]+\.md)`", text):
                        self.assertTrue(Path(reference).is_file(), reference)
                    self.assertFalse((skill_dir / "agents").exists())
            self.assertTrue((target / "testlink-testcase-maintenance" / "references" / "protected-write.md").is_file())

    def test_dry_run_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = self.install(Path(tmp), dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertTrue(result["skills"])
            self.assertEqual([], list(Path(tmp).iterdir()))

    def test_foreign_directory_is_skipped_unless_forced(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp)
            foreign = target / "redmine-issue"
            foreign.mkdir()
            (foreign / "SKILL.md").write_text("someone else's skill", encoding="utf-8")

            result = self.install(target)
            skipped = [item for item in result["skills"] if item["action"] == "skipped"]
            self.assertFalse(result["ok"])
            self.assertEqual(["redmine-issue"], [item["skill"] for item in skipped])
            self.assertEqual("someone else's skill", (foreign / "SKILL.md").read_text(encoding="utf-8"))

            forced = self.install(target, force=True)
            self.assertTrue(forced["ok"])
            self.assertTrue((foreign / MARKER).is_file())

    def test_reinstall_replaces_a_managed_skill_cleanly(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp)
            self.install(target)
            stale = target / "testlink-execution" / "stale.txt"
            stale.write_text("left from an older version", encoding="utf-8")

            result = self.install(target)
            self.assertTrue(result["ok"])
            self.assertEqual({"update"}, {item["action"] for item in result["skills"]})
            self.assertFalse(stale.exists())

    def test_missing_cited_doc_fails_before_writing_that_skill(self) -> None:
        with TemporaryDirectory() as tmp:
            empty_docs = Path(tmp) / "docs"
            empty_docs.mkdir()
            with self.assertRaises(FileNotFoundError):
                install_skills(target=Path(tmp) / "target", source=SKILLS, docs=empty_docs)

    def test_cli_reports_json_and_exit_code(self) -> None:
        with TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["install-skills", "--target", tmp, "--json"])
            self.assertEqual(0, status)
            self.assertTrue(json.loads(output.getvalue())["ok"])


class SkillPackagingTests(unittest.TestCase):
    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_pyproject_ships_skills_and_every_cited_doc(self) -> None:
        setuptools = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]
        self.assertIn("testlink_agent_skills", setuptools["packages"])
        self.assertIn("testlink_agent_docs", setuptools["packages"])
        self.assertEqual(".agents/skills", setuptools["package-dir"]["testlink_agent_skills"])
        self.assertEqual("docs", setuptools["package-dir"]["testlink_agent_docs"])
        self.assertIn("**/*.md", setuptools["package-data"]["testlink_agent_skills"])
        self.assertIn("*.md", setuptools["package-data"]["testlink_agent_docs"])
        for skill in SKILLS.iterdir():
            skill_md = skill / "SKILL.md"
            if not skill_md.is_file():
                continue
            for name in re.findall(r"docs/([\w.-]+\.md)", skill_md.read_text(encoding="utf-8")):
                with self.subTest(skill=skill.name, doc=name):
                    self.assertTrue((DOCS / name).is_file())


if __name__ == "__main__":
    unittest.main()
