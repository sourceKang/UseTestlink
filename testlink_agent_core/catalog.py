from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .clients import TestLinkClient
from .errors import TestLinkError
from .suites import collect_test_suites, suite_search_row, text_contains


def build_catalog(
    client: TestLinkClient,
    project_contains: str | None = None,
    active_only: bool = True,
    recursive: bool = True,
    max_projects: int | None = None,
) -> dict[str, Any]:
    projects = [
        project
        for project in client.get_projects()
        if text_contains(project.get("name"), project_contains)
        and (not active_only or str(project.get("active")) == "1")
    ]
    projects = sorted(projects, key=lambda project: str(project.get("name") or "").casefold())
    if max_projects and len(projects) > max_projects:
        projects = projects[:max_projects]

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for project in projects:
        project_id = str(project.get("id") or "")
        if not project_id:
            continue
        try:
            suites = collect_test_suites(client, project_id, recursive=recursive)
        except Exception as exc:
            failures.append({"project": project.get("name"), "error": str(exc)})
            continue
        rows.append(
            {
                "id": project.get("id"),
                "name": project.get("name"),
                "prefix": project.get("prefix"),
                "active": project.get("active"),
                "is_public": project.get("is_public"),
                "suites": [
                    {
                        "id": suite.get("id"),
                        "name": suite.get("name"),
                        "parent_id": suite.get("parent_id"),
                        "path": suite.get("path"),
                        "depth": suite.get("depth"),
                        "node_order": suite.get("node_order"),
                    }
                    for suite in suites
                ],
            }
        )
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "TestLink XML-RPC",
        "active_only": active_only,
        "recursive": recursive,
        "project_contains": project_contains,
        "project_count": len(rows),
        "projects": rows,
        "scan_failures": failures,
    }

def read_catalog(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise TestLinkError(f"Catalog file does not exist: {path}. Run refresh-catalog first.")
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TestLinkError(f"Catalog file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(catalog, dict) or not isinstance(catalog.get("projects"), list):
        raise TestLinkError(f"Catalog file has unexpected format: {path}")
    return catalog

def write_catalog(catalog: dict[str, Any], path: Path, force: bool = False) -> None:
    if path.exists() and not force:
        raise TestLinkError(f"Catalog file already exists: {path}. Use --force to overwrite.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")

def find_suites_in_catalog(
    catalog: dict[str, Any],
    project_contains: str | None,
    suite_contains: str | None,
    active_only: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    scanned_projects: list[dict[str, Any]] = []
    for project in catalog.get("projects") or []:
        if not isinstance(project, dict):
            continue
        if not text_contains(project.get("name"), project_contains):
            continue
        if active_only and str(project.get("active")) != "1":
            continue
        scanned_projects.append(
            {
                "id": project.get("id"),
                "name": project.get("name"),
                "active": project.get("active"),
            }
        )
        for suite in project.get("suites") or []:
            if not isinstance(suite, dict):
                continue
            if text_contains(suite.get("name"), suite_contains) or text_contains(suite.get("path"), suite_contains):
                matches.append(suite_search_row(project, suite))
    return matches, scanned_projects
