"""Fail when a git-ignored sensitive path is tracked or staged (e.g. via git add -f)."""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SENSITIVE = re.compile(
    r"^(\.env$|\.env\.bak\.|local/|downloads/|reports/|outputs?/|github_upload|\.codex/|\.mcp\.json$)"
    r"|(^|/)CLAUDE\.local\.md$|\.local\."
)


def tracked_paths():
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in result.stdout.decode("utf-8").split("\0") if p]


class SensitivePathTests(unittest.TestCase):
    def test_no_sensitive_paths_tracked_or_staged(self):
        paths = tracked_paths()
        if paths is None:
            self.skipTest("not a git checkout")
        self.assertEqual([], [p for p in paths if SENSITIVE.search(p)])

    def test_pattern_matches_expected_paths(self):
        for path in [".env", ".env.bak.1", "local/note.md", "reports/a.html", "output/x", "outputs/x",
                     ".codex/config.toml", ".mcp.json", "CLAUDE.local.md", "a.local.json"]:
            self.assertRegex(path, SENSITIVE)
        for path in [".env.example", "docs/claude-setup.md", "tests/test_qa_ports.py",
                     "docs/claude-code-mcp.example.json"]:
            self.assertNotRegex(path, SENSITIVE)


if __name__ == "__main__":
    unittest.main()
