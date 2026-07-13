from __future__ import annotations

from typing import Any

from testlink_agent_core.errors import mask_secrets, redact_secrets


class CoordinatorError(RuntimeError):
    def __init__(self, message: str, *, code: str = "COORDINATOR_ERROR", retryable: bool = False):
        super().__init__(mask_secrets(message))
        self.message = mask_secrets(message)
        self.code = code
        self.retryable = retryable

    def to_safe_error(self, stage: str) -> dict[str, Any]:
        return redact_secrets(
            {
                "code": self.code,
                "message": self.message,
                "stage": stage,
                "retryable": self.retryable,
            }
        )


def normalize_error(error: BaseException, stage: str) -> dict[str, Any]:
    if isinstance(error, CoordinatorError):
        return error.to_safe_error(stage)
    return CoordinatorError(str(error)).to_safe_error(stage)
