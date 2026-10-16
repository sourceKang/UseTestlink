"""Toolset selection shared by the MCP servers.

A toolset variable holds one name or a comma-separated list of names. Each name is
either one of the server's toolsets or a preset that bundles several of them for a
usage profile; the selected tools are the union of every name.
"""

from __future__ import annotations

from collections.abc import Mapping


PRESET_NAMES = ("batch-import", "manual-qa", "bug-tracking")


def toolset_parts(value: str | None) -> list[str]:
    return [part.strip().casefold() for part in str(value or "").split(",")]


def resolve_tool_names(
    value: str | None,
    *,
    toolsets: Mapping[str, set[str]],
    presets: Mapping[str, tuple[str, ...]],
    env_name: str,
) -> set[str]:
    parts = toolset_parts(value)
    choices = ", ".join([*sorted(toolsets), *sorted(presets)])
    if not parts or any(not part for part in parts):
        raise ValueError(f"{env_name} must name one or more of: {choices}.")
    names: set[str] = set()
    for part in parts:
        if part in toolsets:
            names |= toolsets[part]
        elif part in presets:
            for toolset in presets[part]:
                names |= toolsets[toolset]
        else:
            raise ValueError(f"{env_name} must name one or more of: {choices}.")
    return names
