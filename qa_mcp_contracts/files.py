from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path


class LocalPathError(OSError):
    """A local audit or preview location could not be created or written.

    The message always carries the absolute path, because a relative default
    resolves against whatever working directory the MCP client chose (Claude
    Desktop uses C:\\Windows\\System32) and the bare OS error names only "local".
    """


def ensure_directory(directory: str | Path, *, label: str) -> Path:
    """Create `directory` (and parents) or raise LocalPathError naming its absolute path."""

    path = Path(directory)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LocalPathError(f"{label} directory is not writable: {os.path.abspath(path)} ({exc})") from exc
    return path


def write_text_atomically(path: str | Path, text: str, *, label: str) -> None:
    """Write `text` via a temp file and atomic replace, naming the absolute path on failure."""

    target = Path(path)
    temp_path = target.with_name(target.name + ".tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        atomic_replace(temp_path, target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise LocalPathError(f"{label} file could not be written: {os.path.abspath(target)} ({exc})") from exc


def atomic_replace(temp_path: str | Path, target_path: str | Path, *, attempts: int = 4) -> None:
    """Replace a file atomically, tolerating brief Windows file-scanner locks."""

    temp = Path(temp_path)
    target = Path(target_path)
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(attempts):
        try:
            temp.replace(target)
            return
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.02 * (2**attempt))
