from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_PROFILES_PATH
from .errors import TestLinkError


def profiles_path(value: str | None = None) -> Path:
    return Path(value or DEFAULT_PROFILES_PATH)

def empty_profiles() -> dict[str, Any]:
    return {"version": 1, "updated_at": None, "profiles": {}}

def read_profiles(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_profiles()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TestLinkError(f"Profile file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        raise TestLinkError(f"Profile file has unexpected format: {path}")
    return data

def write_profiles(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = 1
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def validate_profile_name(name: str) -> str:
    profile_name = name.strip()
    if not profile_name:
        raise TestLinkError("Profile name is required.")
    return profile_name

def profile_from_values(
    *,
    project: str,
    suite_id: str,
    suite_name: str | None = None,
    suite_path: str | None = None,
    testprojectid: str | None = None,
) -> dict[str, Any]:
    project = project.strip()
    suite_id = str(suite_id).strip()
    if not project:
        raise TestLinkError("Profile project is required.")
    if not suite_id:
        raise TestLinkError("Profile suite ID is required.")
    profile: dict[str, Any] = {
        "project": project,
        "suite_id": suite_id,
    }
    if suite_name:
        profile["suite_name"] = suite_name
    if suite_path:
        profile["suite_path"] = suite_path
    if testprojectid:
        profile["testprojectid"] = str(testprojectid)
    return profile

def profile_from_suite_search_row(row: dict[str, Any]) -> dict[str, Any]:
    return profile_from_values(
        project=str(row.get("project") or ""),
        suite_id=str(row.get("suite_id") or ""),
        suite_name=str(row.get("suite_name") or ""),
        suite_path=str(row.get("suite_path") or ""),
        testprojectid=str(row.get("testprojectid") or ""),
    )

def save_profile(path: Path, name: str, profile: dict[str, Any], force: bool = False) -> dict[str, Any]:
    profile_name = validate_profile_name(name)
    data = read_profiles(path)
    profiles = data.setdefault("profiles", {})
    if profile_name in profiles and not force:
        raise TestLinkError(f"Profile already exists: {profile_name}. Use --force to overwrite.")
    profiles[profile_name] = profile
    write_profiles(data, path)
    return profile

def get_profile(path: Path, name: str) -> dict[str, Any]:
    profile_name = validate_profile_name(name)
    data = read_profiles(path)
    profile = data.get("profiles", {}).get(profile_name)
    if not isinstance(profile, dict):
        raise TestLinkError(f"Profile not found: {profile_name}")
    return profile

def delete_profile(path: Path, name: str) -> dict[str, Any]:
    profile_name = validate_profile_name(name)
    data = read_profiles(path)
    profiles = data.setdefault("profiles", {})
    profile = profiles.pop(profile_name, None)
    if not isinstance(profile, dict):
        raise TestLinkError(f"Profile not found: {profile_name}")
    write_profiles(data, path)
    return profile

def list_profiles(path: Path) -> list[dict[str, Any]]:
    data = read_profiles(path)
    rows: list[dict[str, Any]] = []
    for name, profile in sorted(data.get("profiles", {}).items()):
        if not isinstance(profile, dict):
            continue
        rows.append(
            {
                "name": name,
                "project": profile.get("project"),
                "testprojectid": profile.get("testprojectid"),
                "suite_id": profile.get("suite_id"),
                "suite_name": profile.get("suite_name"),
                "suite_path": profile.get("suite_path"),
                "create_example": (
                    f'python .\\testlink_agent.py create-testcase --profile "{name}" '
                    f'--name "your_testcase_name" --summary "your summary" '
                    f'--step "Action => Expected result"'
                ),
            }
        )
    return rows

def apply_create_profile(args: argparse.Namespace) -> dict[str, Any] | None:
    profile_name = getattr(args, "profile", None)
    if not profile_name:
        return None
    profile = get_profile(profiles_path(getattr(args, "profiles", None)), profile_name)
    if not getattr(args, "project", None):
        args.project = profile.get("project")
    if not getattr(args, "suite_id", None) and not getattr(args, "suite_name", None):
        args.suite_id = profile.get("suite_id")
        if not args.suite_id:
            args.suite_name = profile.get("suite_name") or profile.get("suite_path")
    return profile
