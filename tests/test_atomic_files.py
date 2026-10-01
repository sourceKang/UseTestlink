from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from qa_mcp_contracts.files import LocalPathError, atomic_replace, ensure_directory, write_text_atomically


class AtomicReplaceTests(unittest.TestCase):
    def test_retries_transient_permission_error(self) -> None:
        with TemporaryDirectory() as tmpdir:
            temp = Path(tmpdir) / "audit.json.tmp"
            target = Path(tmpdir) / "audit.json"
            temp.write_text("safe audit", encoding="utf-8")
            real_replace = Path.replace
            attempts = 0

            def flaky_replace(path: Path, destination: Path):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("temporarily locked")
                return real_replace(path, destination)

            with patch.object(Path, "replace", new=flaky_replace), patch(
                "qa_mcp_contracts.files.time.sleep"
            ) as sleep:
                atomic_replace(temp, target)

            self.assertEqual(2, attempts)
            sleep.assert_called_once_with(0.02)
            self.assertEqual("safe audit", target.read_text(encoding="utf-8"))

    def test_exhausted_permission_error_is_not_hidden(self) -> None:
        with TemporaryDirectory() as tmpdir:
            temp = Path(tmpdir) / "audit.json.tmp"
            target = Path(tmpdir) / "audit.json"
            temp.write_text("safe audit", encoding="utf-8")
            with patch.object(Path, "replace", side_effect=PermissionError("locked")), patch(
                "qa_mcp_contracts.files.time.sleep"
            ):
                with self.assertRaises(PermissionError):
                    atomic_replace(temp, target, attempts=2)

            self.assertTrue(temp.exists())
            self.assertFalse(target.exists())


class LocalPathErrorTests(unittest.TestCase):
    """Every audit and preview writer names the absolute path it could not use."""

    def _writers(self):
        from qa_integration_agent.artifacts import write_preview_artifact
        from qa_integration_agent.audit import write_workflow_audit
        from redmine_mcp.audit import write_operation_audit as write_redmine_audit
        from testlink_mcp.audit import write_operation_audit as write_testlink_audit

        record = {"operation_id": "operation-unwritable", "action": "append-execution"}
        return {
            "TestLink audit": lambda directory: write_testlink_audit(record, directory),
            "Redmine audit": lambda directory: write_redmine_audit(record, directory),
            "Workflow audit": lambda directory: write_workflow_audit(record, directory),
            "Preview artifact": lambda directory: write_preview_artifact(record, {}, directory),
        }

    def test_unwritable_directory_error_carries_the_absolute_path(self) -> None:
        # The bare OS error names only "local"; the absolute path is what makes it diagnosable.
        relative = Path("local") / "audit"
        for label, write in self._writers().items():
            with self.subTest(writer=label), \
                    patch("pathlib.Path.mkdir", side_effect=PermissionError(5, "Access is denied", "local")):
                with self.assertRaises(LocalPathError) as context:
                    write(relative)
                message = str(context.exception)
                self.assertIn(f"{label} directory is not writable: {os.path.abspath(relative)}", message)
                self.assertIn("Access is denied", message)
                self.assertIsInstance(context.exception, OSError)

    def test_file_write_failure_names_the_absolute_target(self) -> None:
        with TemporaryDirectory() as tmpdir, patch(
            "pathlib.Path.write_text", side_effect=PermissionError(13, "Permission denied")
        ):
            target = Path(tmpdir) / "audit.json"
            with self.assertRaises(LocalPathError) as context:
                write_text_atomically(target, "{}", label="TestLink audit")

        self.assertIn(f"TestLink audit file could not be written: {target}", str(context.exception))

    def test_ensure_directory_creates_nested_directories(self) -> None:
        with TemporaryDirectory() as tmpdir:
            created = ensure_directory(Path(tmpdir) / "a" / "b", label="TestLink audit")
            self.assertTrue(created.is_dir())


if __name__ == "__main__":
    unittest.main()
