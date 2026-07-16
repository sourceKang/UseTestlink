from __future__ import annotations

import math
import unittest

from qa_mcp_contracts import (
    ContractError,
    assert_safe_contract,
    canonical_json,
    payload_digest,
    validate_operation_context,
    validate_preview_digest,
)


class ContractCoreTests(unittest.TestCase):
    def test_digest_is_stable_across_mapping_order(self) -> None:
        left = {"operation_id": "operation-123", "target": {"b": 2, "a": 1}}
        right = {"target": {"a": 1, "b": 2}, "operation_id": "operation-123"}
        self.assertEqual(payload_digest(left), payload_digest(right))

    def test_digest_changes_when_planned_write_changes(self) -> None:
        previewed = {"operation_id": "operation-123", "status": "f"}
        changed = {"operation_id": "operation-123", "status": "p"}
        self.assertNotEqual(payload_digest(previewed), payload_digest(changed))

    def test_preview_digest_accepts_exact_planned_payload(self) -> None:
        payload = {"operation_id": "operation-123", "environment": "sandbox"}
        digest = payload_digest(payload)
        self.assertEqual(digest, validate_preview_digest(payload, digest))

    def test_preview_digest_rejects_changed_payload(self) -> None:
        previewed = {"operation_id": "operation-123", "status": "f"}
        changed = {"operation_id": "operation-123", "status": "p"}
        with self.assertRaisesRegex(ContractError, "does not match"):
            validate_preview_digest(changed, payload_digest(previewed))

    def test_preview_digest_rejects_missing_or_malformed_digest(self) -> None:
        payload = {"operation_id": "operation-123"}
        for digest in ("", "ABC", "f" * 63, "G" * 64):
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(ContractError, "64-character"):
                    validate_preview_digest(payload, digest)

    def test_secret_bearing_field_names_are_rejected_recursively(self) -> None:
        unsafe_values = [
            {"REDMINE_API_KEY": "masked-or-not"},
            {"nested": {"testlink_devkey": "masked-or-not"}},
            {"items": [{"password": "masked-or-not"}]},
            {"authorization_header": "masked-or-not"},
        ]
        for value in unsafe_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ContractError, "Secret-bearing"):
                    assert_safe_contract(value)

    def test_operation_context_is_explicit_and_fail_closed(self) -> None:
        context = {
            "schema_version": "1.0",
            "operation_id": "operation-123",
            "correlation_id": "correlation-123",
            "environment": "CORP",
            "requested_at": "2026-07-13T12:00:00Z",
            "source": "qa-integration-agent",
        }
        validated = validate_operation_context(context)
        self.assertEqual("corp", validated["environment"])

        for field, value in (
            ("schema_version", "2.0"),
            ("operation_id", "short"),
            ("correlation_id", "short"),
            ("environment", "production"),
        ):
            invalid = {**context, field: value}
            with self.subTest(field=field):
                with self.assertRaises(ContractError):
                    validate_operation_context(invalid)

    def test_canonical_json_rejects_non_json_and_non_finite_numbers(self) -> None:
        for value in ({"value": object()}, {"value": math.nan}, {"value": math.inf}):
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    canonical_json(value)


if __name__ == "__main__":
    unittest.main()
