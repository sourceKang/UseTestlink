import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from testlink_agent_core.config import (
    DEFAULT_ENV_FILE_PATH,
    ENV_FILE_POINTER,
    load_env_files,
    load_testlink_settings,
)
from testlink_agent_core.errors import TestLinkError


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.saved_env = {
            key: os.environ.get(key)
            for key in (
                "TESTLINK_URL",
                "TESTLINK_DEVKEY",
                "TESTLINK_AUTHOR_LOGIN",
                "REDMINE_API_KEY",
                ENV_FILE_POINTER,
            )
        }
        for key in self.saved_env:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_loads_default_local_record_file(self):
        with TemporaryDirectory() as tmpdir:
            cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                local = Path("local")
                local.mkdir()
                record = local / "testlink_agent.env"
                record.write_text(
                    "TESTLINK_URL=https://testlink.example.com/testlink\n"
                    "TESTLINK_DEVKEY=replace-with-test-key\n",
                    encoding="utf-8",
                )

                loaded = load_env_files(None)

                self.assertEqual(loaded, [str(record)])
                self.assertEqual(os.environ["TESTLINK_URL"], "https://testlink.example.com/testlink")
                self.assertEqual(os.environ["TESTLINK_DEVKEY"], "replace-with-test-key")
            finally:
                os.chdir(cwd)

    def test_env_file_pointer_selects_shared_record_file(self):
        with TemporaryDirectory() as tmpdir:
            record = Path(tmpdir) / "shared.env"
            record.write_text("TESTLINK_AUTHOR_LOGIN=alice\n", encoding="utf-8")
            os.environ[ENV_FILE_POINTER] = str(record)

            loaded = load_env_files(None)

            self.assertEqual(loaded, [str(record)])
            self.assertEqual(os.environ["TESTLINK_AUTHOR_LOGIN"], "alice")

    def test_project_root_record_file_is_fallback_when_cwd_has_no_env(self):
        with TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "other-project"
            project_root = Path(tmpdir) / "use-testlink"
            workspace.mkdir()
            record = project_root / DEFAULT_ENV_FILE_PATH
            record.parent.mkdir(parents=True)
            record.write_text("REDMINE_API_KEY=replace-with-redmine-key\n", encoding="utf-8")

            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                with patch("testlink_agent_core.config.PROJECT_ROOT", project_root):
                    loaded = load_env_files(None)

                self.assertEqual(loaded, [str(record)])
                self.assertEqual(os.environ["REDMINE_API_KEY"], "replace-with-redmine-key")
            finally:
                os.chdir(cwd)

    def test_explicit_env_file_is_used_first(self):
        with TemporaryDirectory() as tmpdir:
            explicit = Path(tmpdir) / "explicit.env"
            explicit.write_text("TESTLINK_URL=https://explicit.example.com/testlink\n", encoding="utf-8")

            loaded = load_env_files(str(explicit))

            self.assertEqual(loaded, [str(explicit)])
            self.assertEqual(os.environ["TESTLINK_URL"], "https://explicit.example.com/testlink")

    def test_env_file_replaces_empty_environment_value(self):
        with TemporaryDirectory() as tmpdir:
            explicit = Path(tmpdir) / "explicit.env"
            explicit.write_text("REDMINE_API_KEY=replace-with-redmine-key\n", encoding="utf-8")
            os.environ["REDMINE_API_KEY"] = ""

            load_env_files(str(explicit))

            self.assertEqual(os.environ["REDMINE_API_KEY"], "replace-with-redmine-key")

    def test_env_file_keeps_nonempty_environment_value(self):
        with TemporaryDirectory() as tmpdir:
            explicit = Path(tmpdir) / "explicit.env"
            explicit.write_text("REDMINE_API_KEY=replace-with-redmine-key\n", encoding="utf-8")
            os.environ["REDMINE_API_KEY"] = "already-set"

            load_env_files(str(explicit))

            self.assertEqual(os.environ["REDMINE_API_KEY"], "already-set")

    def test_missing_env_file_pointer_raises(self):
        os.environ[ENV_FILE_POINTER] = r"C:\missing\testlink_agent.env"

        with self.assertRaises(TestLinkError):
            load_env_files(None)

    def test_load_testlink_settings_reads_only_environment_values(self):
        os.environ["TESTLINK_URL"] = "https://testlink.example.com/testlink"
        os.environ["TESTLINK_DEVKEY"] = "replace-with-test-key"

        settings = load_testlink_settings(timeout=12)

        self.assertEqual(settings.url, "https://testlink.example.com/testlink")
        self.assertEqual(settings.devkey, "replace-with-test-key")
        self.assertEqual(settings.timeout, 12)


if __name__ == "__main__":
    unittest.main()
