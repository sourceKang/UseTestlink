from __future__ import annotations

import os
import re
import xmlrpc.client
from typing import Any


MASK = "*****"
_SECRET_KEY_PARTS = ("devkey", "api_key", "password", "token", "secret")


class TestLinkError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | str | None = None,
        raw: Any | None = None,
        possible_causes: list[str] | None = None,
    ):
        super().__init__(mask_secrets(message))
        self.message = mask_secrets(message)
        self.code = code
        self.raw = redact_secrets(raw)
        self.possible_causes = possible_causes or []

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": type(self).__name__,
            "message": self.message,
        }
        if self.code is not None:
            payload["code"] = self.code
        if self.possible_causes:
            payload["possible_causes"] = self.possible_causes
        if self.raw is not None:
            payload["raw"] = self.raw
        return payload


class RedmineError(RuntimeError):
    pass


def _known_secret_values() -> list[str]:
    values: list[str] = []
    for env_name in ("TESTLINK_DEVKEY", "REDMINE_API_KEY"):
        value = os.environ.get(env_name, "").strip()
        if value:
            values.append(value)
    return values


def is_secret_key(key: str | None) -> bool:
    return bool(key) and any(part in key.casefold() for part in _SECRET_KEY_PARTS)


def mask_secrets(value: Any) -> str:
    text = str(value)
    for secret in _known_secret_values():
        text = text.replace(secret, MASK)
    text = re.sub(r"(devKey\s*['\"]?\s*[:=]\s*['\"]?)([^,'\"\s}]+)", rf"\1{MASK}", text, flags=re.IGNORECASE)
    text = re.sub(r"(TESTLINK_DEVKEY\s*=\s*)(.+)", rf"\1{MASK}", text, flags=re.IGNORECASE)
    text = re.sub(r"(REDMINE_API_KEY\s*=\s*)(.+)", rf"\1{MASK}", text, flags=re.IGNORECASE)
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


def likely_testlink_causes(message: str) -> list[str]:
    text = message.casefold()
    causes: list[str] = []
    if "not found" in text or "does not exist" in text or "不存在" in text:
        causes.append("id 或名稱不存在")
    if "test plan" in text or "testplan" in text:
        causes.append("testcase 版本可能尚未加入指定 test plan")
    if "permission" in text or "right" in text or "access" in text or "denied" in text:
        causes.append("使用者權限不足或 devKey 無效")
    return causes


def normalize_testlink_error(error: BaseException) -> TestLinkError:
    if isinstance(error, TestLinkError):
        return error
    if isinstance(error, xmlrpc.client.Fault):
        message = mask_secrets(error.faultString)
        return TestLinkError(
            message,
            code=error.faultCode,
            raw={"fault_code": error.faultCode, "fault_message": message},
            possible_causes=likely_testlink_causes(message),
        )
    message = mask_secrets(str(error))
    return TestLinkError(message, possible_causes=likely_testlink_causes(message))
