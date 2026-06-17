from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedResult:
    external_id: str
    test_name: str
    raw_status: str
    status: str | None
    duration_text: str
    duration_seconds: float | None
    testcase_id: str | None = None
    version: str | None = None
    testlink_name: str | None = None


@dataclass
class RedmineIssue:
    id: str
    url: str
    subject: str
    reused: bool = False
