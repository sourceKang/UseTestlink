from __future__ import annotations

from .server import TOOLS, handle_request, main, run, startup_health_check

__all__ = ["TOOLS", "handle_request", "main", "run", "startup_health_check"]


if __name__ == "__main__":
    raise SystemExit(main())
