from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from testlink_agent_core.cli import main
from testlink_agent_core.doctor import diagnose


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env_file = Path(self.directory.name) / "fixture.env"
        self.env_file.write_text("TESTLINK_DEVKEY=never-read-this-value", encoding="utf-8")
        distribution = Mock()
        distribution.version = "1.7.0"
        distribution.locate_file.return_value = Path(__file__).resolve().parent.parent / "testlink_agent_core"
        distribution.read_text.return_value = None
        for target, kwargs in (
            ("testlink_agent_core.doctor.metadata.distribution", {"return_value": distribution}),
            ("testlink_agent_core.doctor.shutil.which", {"return_value": str(Path(self.directory.name) / "server.exe")}),
        ):
            mocked = patch(target, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)
        env_patch = patch.dict(os.environ, {}, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def test_healthy_check_never_reads_credentials_or_connects(self):
        before = dict(os.environ)
        opened = Mock()
        opened.__enter__ = Mock(return_value=opened)
        opened.__exit__ = Mock(return_value=False)
        opened.read.side_effect = AssertionError("credentials must not be read")
        with patch("pathlib.Path.open", return_value=opened), \
             patch("socket.socket", side_effect=AssertionError("offline only")):
            result = diagnose(testlink_env_file=str(self.env_file), redmine_env_file=str(self.env_file))
        self.assertTrue(result["ok"])
        self.assertTrue(result["offline"])
        opened.read.assert_not_called()
        self.assertNotIn("never-read-this-value", json.dumps(result))
        self.assertEqual(before, dict(os.environ))

    def test_missing_and_relative_pointers_have_actionable_errors(self):
        result = diagnose(testlink_env_file="relative.env")
        failures = [c for c in result["checks"] if c["status"] == "error"]
        self.assertFalse(result["ok"])
        self.assertEqual(2, len(failures))
        self.assertTrue(all(c["fix"] for c in failures))

    def test_invalid_toolset_is_safe_and_does_not_echo_value(self):
        with patch.dict(os.environ, {"QA_INTEGRATION_TOOLSET": "private-untrusted-value"}):
            result = diagnose(testlink_env_file=str(self.env_file), redmine_env_file=str(self.env_file))
        self.assertFalse(result["ok"])
        self.assertNotIn("private-untrusted-value", json.dumps(result))

    def test_directory_missing_and_unreadable_files(self):
        for location in (self.directory.name, str(self.env_file.parent / "missing.env")):
            self.assertFalse(diagnose(server="testlink", testlink_env_file=location)["ok"])
        with patch("pathlib.Path.open", side_effect=PermissionError("private-path-secret")):
            result = diagnose(server="testlink", testlink_env_file=str(self.env_file))
        self.assertFalse(result["ok"])
        self.assertNotIn("private-path-secret", json.dumps(result))

    def test_missing_package_and_executable(self):
        with patch("testlink_agent_core.doctor.metadata.distribution", side_effect=metadata.PackageNotFoundError), \
             patch("testlink_agent_core.doctor.shutil.which", return_value=None):
            result = diagnose(server="testlink", testlink_env_file=str(self.env_file))
        failures = {c["check"] for c in result["checks"] if c["status"] == "error"}
        self.assertEqual({"package", "executable"}, failures)

    def test_direct_servers_use_actual_default_all_and_only_own_pointer(self):
        for server, key in (("testlink", "TESTLINK_MCP_ENV_FILE"), ("redmine", "REDMINE_MCP_ENV_FILE")):
            with patch.dict(os.environ, {key: str(self.env_file)}):
                result = diagnose(server=server)
            self.assertTrue(result["ok"])
            toolset = next(c for c in result["checks"] if c["check"] == "toolset")
            self.assertEqual("all", toolset["toolset"])
            self.assertEqual("warning", toolset["status"])

    def test_cli_json_exit_status(self):
        with redirect_stdout(io.StringIO()) as output:
            status = main(["doctor", "--json"])
        self.assertEqual(1, status)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        with redirect_stdout(io.StringIO()) as output:
            status = main(["doctor", "--json", "--testlink-env-file", str(self.env_file),
                           "--redmine-env-file", str(self.env_file)])
        self.assertEqual(0, status)
        self.assertTrue(json.loads(output.getvalue())["ok"])
