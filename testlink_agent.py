#!/usr/bin/env python3
"""Compatibility entrypoint for TestLink Agent CLI."""

from testlink_agent_core.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
