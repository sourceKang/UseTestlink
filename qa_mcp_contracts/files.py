from __future__ import annotations

import time
from pathlib import Path


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
