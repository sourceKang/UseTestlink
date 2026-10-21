"""Copy the packaged TestLink Agent skills into a user's Claude skills directory.

The skills live in this repository under `.agents/skills` and are packaged as
`testlink_agent_skills`; the docs they cite are packaged as `testlink_agent_docs`.
Teammates install with pipx and have no checkout, so each installed skill gets the
docs it cites copied next to it, and `docs/<name>.md` references in SKILL.md are
rewritten to those copies.

An installed skill carries a marker file. A directory without the marker belongs to
someone else and is never overwritten unless the caller passes force.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
from pathlib import Path
from typing import Any

from qa_mcp_contracts.release import PACKAGE_VERSION


MARKER = ".testlink-agent-skill"
# Repository-maintenance skills that only make sense inside this checkout.
REPO_ONLY_SKILLS = {"retro"}
_DOC_REFERENCE = re.compile(r"docs/([A-Za-z0-9_.-]+\.md)")
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _package_dir(package: str, fallback: Path) -> Path:
    spec = importlib.util.find_spec(package)
    if spec is not None and spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)))
    return fallback


def skills_source() -> Path:
    return _package_dir("testlink_agent_skills", _REPO_ROOT / ".agents" / "skills")


def docs_source() -> Path:
    return _package_dir("testlink_agent_docs", _REPO_ROOT / "docs")


def default_target() -> Path:
    return Path.home() / ".claude" / "skills"


def available_skills(source: Path) -> list[str]:
    return sorted(
        path.name
        for path in source.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file() and path.name not in REPO_ONLY_SKILLS
    )


def _rewrite_doc_references(text: str, docs_dir: Path) -> tuple[str, list[str]]:
    cited: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in cited:
            cited.append(name)
        return (docs_dir / name).as_posix()

    return _DOC_REFERENCE.sub(replace, text), cited


def install_skills(
    *,
    target: Path | None = None,
    force: bool = False,
    dry_run: bool = False,
    source: Path | None = None,
    docs: Path | None = None,
) -> dict[str, Any]:
    source = source or skills_source()
    docs = docs or docs_source()
    target = Path(target or default_target()).expanduser()
    results: list[dict[str, Any]] = []
    for name in available_skills(source):
        destination = target / name
        if destination.exists() and not (destination / MARKER).is_file() and not force:
            results.append(
                {
                    "skill": name,
                    "action": "skipped",
                    "reason": "an existing directory was not installed by testlink-agent; pass --force to replace it",
                }
            )
            continue
        action = "update" if destination.exists() else "install"
        skill_text = (source / name / "SKILL.md").read_text(encoding="utf-8")
        rewritten, cited = _rewrite_doc_references(skill_text, destination / "docs")
        missing = [doc for doc in cited if not (docs / doc).is_file()]
        if missing:
            raise FileNotFoundError(f"Skill {name} cites docs that are not packaged: {', '.join(missing)}")
        results.append({"skill": name, "action": action, "docs": cited})
        if dry_run:
            continue
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text(rewritten, encoding="utf-8", newline="\n")
        references = source / name / "references"
        if references.is_dir():
            shutil.copytree(references, destination / "references")
        if cited:
            (destination / "docs").mkdir()
            for doc in cited:
                shutil.copy2(docs / doc, destination / "docs" / doc)
        (destination / MARKER).write_text(
            json.dumps({"package": "testlink-agent", "version": PACKAGE_VERSION}) + "\n", encoding="utf-8"
        )
    return {
        "ok": not any(item["action"] == "skipped" for item in results),
        "dry_run": dry_run,
        "target": str(target),
        "version": PACKAGE_VERSION,
        "skills": results,
    }


def command_install_skills(args: Any) -> int:
    result = install_skills(
        target=Path(args.target) if args.target else None, force=args.force, dry_run=args.dry_run
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        prefix = "（試跑，未寫入）" if result["dry_run"] else ""
        print(f"{prefix}目標：{result['target']}（testlink-agent {result['version']}）")
        for item in result["skills"]:
            line = f"  [{item['action']}] {item['skill']}"
            if item.get("reason"):
                line += f"：{item['reason']}"
            print(line)
        print("完成後請重新啟動 Claude Code，讓它重新載入 skill。")
    return 0 if result["ok"] else 1
