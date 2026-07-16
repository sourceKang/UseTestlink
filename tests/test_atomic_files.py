from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from qa_mcp_contracts.files import atomic_replace


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


if __name__ == "__main__":
    unittest.main()
