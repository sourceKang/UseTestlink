"""Keep corporate network details out of this public repository.

The generic checks below run everywhere. Site-specific terms (custom endpoint names, internal
project identifiers) must not be written here either, so they are read from the git-ignored
`local/public_repo_denylist.txt`, one regular expression per line; that check is skipped on
machines without the file.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCAL_DENYLIST = ROOT / "local" / "public_repo_denylist.txt"
GENERIC_PATTERNS = {
    "private IPv4 (172.16.0.0/12)": re.compile(r"\b172\.(1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}\b"),
    "private IPv4 (192.168.0.0/16)": re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    "corporate host name": re.compile(r"\b[a-z0-9-]+\.zyxel\.com(\.tw)?\b", re.IGNORECASE),
}


def tracked_text_files() -> list[Path]:
    try:
        output = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        return []
    files = []
    for name in filter(None, output.split("\0")):
        path = ROOT / name
        if path.is_file() and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".whl"}:
            files.append(path)
    return files


def findings(patterns: dict[str, re.Pattern[str]]) -> list[str]:
    found = []
    for path in tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in patterns.items():
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    found.append(f"{path.relative_to(ROOT)}:{number}: {label}")
    return found


class PublicRepoHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        if not tracked_text_files():
            self.skipTest("git is unavailable or this is not a git checkout")

    def test_no_private_addresses_or_corporate_host_names(self) -> None:
        self.assertEqual([], findings(GENERIC_PATTERNS))

    def test_no_terms_from_the_local_denylist(self) -> None:
        if not LOCAL_DENYLIST.is_file():
            self.skipTest("no local/public_repo_denylist.txt on this machine")
        patterns = {
            f"local denylist entry {index}": re.compile(line.strip(), re.IGNORECASE)
            for index, line in enumerate(LOCAL_DENYLIST.read_text(encoding="utf-8").splitlines(), start=1)
            if line.strip() and not line.lstrip().startswith("#")
        }
        # Report only the entry number so a failure message does not repeat the term.
        self.assertEqual([], findings(patterns))


if __name__ == "__main__":
    unittest.main()
