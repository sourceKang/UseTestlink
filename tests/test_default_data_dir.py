"""Default preview and audit directories must not depend on the working directory.

Claude Desktop starts MCP servers in C:\\Windows\\System32, where a relative
default such as local/qa_previews cannot be created, so a preview without
artifact_dir used to fail. The defaults are now absolute per-user paths.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from qa_integration_agent.artifacts import DEFAULT_PREVIEW_DIR
from qa_integration_agent.audit import DEFAULT_AUDIT_DIR as QA_AUDIT_DIR
from qa_mcp_contracts import DATA_DIR_ENV, default_data_dir
from redmine_mcp.config import DEFAULT_AUDIT_DIR as REDMINE_AUDIT_DIR
from testlink_mcp.config import DEFAULT_AUDIT_DIR as TESTLINK_AUDIT_DIR


class DefaultDataDirTests(unittest.TestCase):
    def test_override_env_wins(self) -> None:
        with TemporaryDirectory() as tmpdir, patch.dict(os.environ, {DATA_DIR_ENV: tmpdir, "LOCALAPPDATA": "C:\\elsewhere"}):
            self.assertEqual(str(Path(tmpdir).resolve() / "qa_previews"), default_data_dir("qa_previews"))

    def test_local_app_data_is_used_on_windows(self) -> None:
        with TemporaryDirectory() as tmpdir, patch.dict(os.environ, {DATA_DIR_ENV: "", "LOCALAPPDATA": tmpdir}):
            self.assertEqual(
                str(Path(tmpdir).resolve() / "testlink-agent" / "qa_audit"), default_data_dir("qa_audit")
            )

    def test_home_is_the_last_fallback(self) -> None:
        env = {key: value for key, value in os.environ.items() if key not in (DATA_DIR_ENV, "LOCALAPPDATA")}
        with TemporaryDirectory() as tmpdir, patch.dict(os.environ, env, clear=True), patch.object(
            Path, "home", return_value=Path(tmpdir)
        ):
            self.assertEqual(
                str(Path(tmpdir).resolve() / ".testlink-agent" / "redmine_audit"), default_data_dir("redmine_audit")
            )

    def test_every_mcp_default_is_absolute(self) -> None:
        for default in (DEFAULT_PREVIEW_DIR, QA_AUDIT_DIR, TESTLINK_AUDIT_DIR, REDMINE_AUDIT_DIR):
            self.assertTrue(Path(default).is_absolute(), default)


if __name__ == "__main__":
    unittest.main()
