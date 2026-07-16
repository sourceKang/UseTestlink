from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "contracts" / "v1"
EXPECTED_SCHEMAS = {
    "operation-context.schema.json",
    "error.schema.json",
    "testlink-execution-preview.schema.json",
    "testlink-execution-result.schema.json",
    "redmine-bug-preview.schema.json",
    "redmine-bug-result.schema.json",
    "redmine-comment-preview.schema.json",
    "redmine-comment-result.schema.json",
    "qa-report-preview.schema.json",
    "workflow-audit.schema.json",
}
FORBIDDEN_PROPERTY_FRAGMENTS = {
    "apikey",
    "authorization",
    "devkey",
    "password",
    "secret",
    "token",
}


def load_schemas() -> dict[str, dict[str, Any]]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in CONTRACT_DIR.glob("*.schema.json")
    }


def iter_object_schemas(value: Any):
    if isinstance(value, dict):
        if value.get("type") == "object" or "properties" in value:
            yield value
        for child in value.values():
            yield from iter_object_schemas(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_object_schemas(child)


class ContractSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schemas = load_schemas()

    def test_expected_v1_schemas_exist(self) -> None:
        self.assertEqual(EXPECTED_SCHEMAS, set(self.schemas))

    def test_schemas_use_draft_2020_12_and_unique_ids(self) -> None:
        ids: set[str] = set()
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                self.assertEqual(
                    "https://json-schema.org/draft/2020-12/schema",
                    schema.get("$schema"),
                )
                schema_id = schema.get("$id")
                self.assertIsInstance(schema_id, str)
                self.assertNotIn(schema_id, ids)
                ids.add(schema_id)

    def test_all_object_schemas_reject_unknown_fields(self) -> None:
        for name, schema in self.schemas.items():
            for object_schema in iter_object_schemas(schema):
                with self.subTest(schema=name, title=object_schema.get("title")):
                    self.assertFalse(object_schema.get("additionalProperties"))

    def test_required_fields_are_declared_properties(self) -> None:
        for name, schema in self.schemas.items():
            for object_schema in iter_object_schemas(schema):
                required = set(object_schema.get("required", []))
                properties = set(object_schema.get("properties", {}))
                with self.subTest(schema=name, required=sorted(required)):
                    self.assertTrue(required.issubset(properties))

    def test_contracts_do_not_define_secret_properties(self) -> None:
        for name, schema in self.schemas.items():
            for object_schema in iter_object_schemas(schema):
                for property_name in object_schema.get("properties", {}):
                    normalized = property_name.casefold().replace("_", "").replace("-", "")
                    with self.subTest(schema=name, property=property_name):
                        self.assertFalse(
                            any(fragment in normalized for fragment in FORBIDDEN_PROPERTY_FRAGMENTS)
                        )

    def test_every_contract_has_version_and_operation_identity(self) -> None:
        for name, schema in self.schemas.items():
            properties = schema.get("properties", {})
            required = set(schema.get("required", []))
            with self.subTest(schema=name):
                self.assertEqual("1.0", properties["schema_version"]["const"])
                self.assertIn("schema_version", required)
                self.assertIn("operation_id", required)

    def test_preview_contracts_enforce_preview_first_fields(self) -> None:
        preview_names = {
            "testlink-execution-preview.schema.json",
            "redmine-bug-preview.schema.json",
            "redmine-comment-preview.schema.json",
            "qa-report-preview.schema.json",
        }
        for name in preview_names:
            schema = self.schemas[name]
            required = set(schema["required"])
            with self.subTest(schema=name):
                self.assertEqual("preview", schema["properties"]["mode"]["const"])
                self.assertTrue({"mode", "preview_digest", "planned_write"}.issubset(required))


if __name__ == "__main__":
    unittest.main()
