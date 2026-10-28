"""Single source of the testlink-agent release version.

pyproject.toml reads this value, and every MCP server reports it as its version,
so the number a client shows is the release tag that was installed.
"""

from __future__ import annotations


PACKAGE_VERSION = "1.9.7"
