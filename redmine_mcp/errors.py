from __future__ import annotations

import os
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
