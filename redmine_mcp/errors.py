from __future__ import annotations

import os
import re
from typing import Any

from testlink_agent_core.errors import mask_secret_assignments


MASK = "*****"
_SECRET_KEY_PARTS = ("api_key", "apikey", "authorization", "devkey", "password", "token", "secret")


def _known_secret_values() -> list[str]:
    return [
        value
        for value in (
            os.environ.get("REDMINE_API_KEY", "").strip(),
            os.environ.get("TESTLINK_DEVKEY", "").strip(),
        )
        if value
    ]


def is_secret_key(key: str | None) -> bool:
    normalized = str(key or "").casefold().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def mask_secrets(value: Any) -> str:
    text = str(value)
    for secret in _known_secret_values():
        text = text.replace(secret, MASK)
    text = mask_secret_assignments(
        text, "TESTLINK_DEVKEY|REDMINE_API_KEY|devKey|X-Redmine-API-Key|token",
    )
    return text


# Credential values are ASCII; stopping at non-ASCII keeps adjacent Chinese text.
_VALUE = r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:(?![\s,'"{}\[\]\\])[!-~])+)"""
_CONTENT_ASSIGNMENT_RE = re.compile(
    rf"""((?<![A-Za-z])(?:password|passwd|pwd|secret|api[_-]?key|token)\s*['"]?\s*[:=]\s*){_VALUE}""",
    re.IGNORECASE,
)
_UNMASKED = r"""(?!["']?\*{5})"""
_LONG_PASSWORD_FLAG_RE = re.compile(
    rf"""((?<!\S)--(?:password|passwd|pass)(?:\s+|=)){_UNMASKED}{_VALUE}""", re.IGNORECASE
)
_SSHPASS_RE = re.compile(rf"""(\bsshpass\s+-p\s*){_UNMASKED}{_VALUE}""")
_SHORT_PASSWORD_FLAG_RE = re.compile(rf"""((?<!\S)-p\s+){_UNMASKED}{_VALUE}""")
# "-p" is a port for ssh, so it is treated as a password only beside a user flag.
_USER_FLAG_RE = re.compile(r"(?<!\S)(?:-u|--user|--username)(?:\s|=)")
_URL_USERINFO_RE = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(?=@)", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+(@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")


def _mask_value(match: re.Match[str]) -> str:
    value = match.group(2)
    quote = value[0] if value[0] in "\"'" else ""
    return match.group(1) + quote + MASK + quote


def mask_embedded_credentials(text: str) -> tuple[str, int]:
    total = 0
    for pattern in (_CONTENT_ASSIGNMENT_RE, _LONG_PASSWORD_FLAG_RE, _SSHPASS_RE):
        text, count = pattern.subn(_mask_value, text)
        total += count
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if _USER_FLAG_RE.search(line):
            lines[index], count = _SHORT_PASSWORD_FLAG_RE.subn(_mask_value, line)
            total += count
    text = "\n".join(lines)
    text, count = _URL_USERINFO_RE.subn(lambda match: match.group(1) + MASK, text)
    return text, total + count


def mask_email_addresses(text: str) -> tuple[str, int]:
    return _EMAIL_RE.subn(lambda match: MASK + match.group(1), text)


def redact_secrets(value: Any, key: str | None = None) -> Any:
    if is_secret_key(key):
        return MASK if value not in (None, "") else value
    if isinstance(value, dict):
        return {str(item_key): redact_secrets(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        return mask_secrets(value)
    return value


class RedmineMcpError(RuntimeError):
    def __init__(self, message: str, *, code: str = "REDMINE_ERROR", retryable: bool = False):
        super().__init__(mask_secrets(message))
        self.message = mask_secrets(message)
        self.code = code
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


def normalize_error(error: BaseException) -> RedmineMcpError:
    if isinstance(error, RedmineMcpError):
        return error
    return RedmineMcpError(str(error))
