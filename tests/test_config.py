import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from testlink_agent_core.config import ENV_FILE_POINTER, load_env_files
from testlink_agent_core.errors import TestLinkError


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.saved_env = {
            key: os.environ.get(key)
            for key in ("TESTLINK_URL", "TESTLINK_DEVKEY", "TESTLINK_AUTHOR_LOGIN", ENV_FILE_POINTER)
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

    def test_explicit_env_file_is_used_first(self):
        with TemporaryDirectory() as tmpdir:
            explicit = Path(tmpdir) / "explicit.env"
            explicit.write_text("TESTLINK_URL=https://explicit.example.com/testlink\n", encoding="utf-8")

            loaded = load_env_files(str(explicit))

            self.assertEqual(loaded, [str(explicit)])
            self.assertEqual(os.environ["TESTLINK_URL"], "https://explicit.example.com/testlink")

    def test_missing_env_file_pointer_raises(self):
        os.environ[ENV_FILE_POINTER] = r"C:\missing\testlink_agent.env"

        with self.assertRaises(TestLinkError):
            load_env_files(None)


if __name__ == "__main__":
    unittest.main()
