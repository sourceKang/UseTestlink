from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RedmineIssue:
    id: str
    url: str
    subject: str
    state: str = "open"
    reused: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "url": self.url,
            "subject": self.subject,
            "state": self.state,
            "reused": self.reused,
        }
