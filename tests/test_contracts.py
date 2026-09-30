from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from qa_mcp_contracts import payload_digest
from qa_integration_agent import api
from qa_integration_agent.coordinator import QaCoordinator


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


V2_CONTRACT_DIR = ROOT / "contracts" / "v2"
V2_EXPECTED_SCHEMAS = {
    "qa-report-preview.schema.json",
    "workflow-audit.schema.json",
}


def load_schemas(directory: Path = CONTRACT_DIR) -> dict[str, dict[str, Any]]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in directory.glob("*.schema.json")
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
    DIRECTORY = CONTRACT_DIR
    EXPECTED = EXPECTED_SCHEMAS
    VERSION = "1.0"
    PREVIEWS = {
        "testlink-execution-preview.schema.json",
        "redmine-bug-preview.schema.json",
        "redmine-comment-preview.schema.json",
        "qa-report-preview.schema.json",
    }

    def setUp(self) -> None:
        self.schemas = load_schemas(self.DIRECTORY)

    def test_expected_schemas_exist(self) -> None:
        self.assertEqual(self.EXPECTED, set(self.schemas))

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
                self.assertEqual(self.VERSION, properties["schema_version"]["const"])
                self.assertIn("schema_version", required)
                self.assertIn("operation_id", required)

    def test_preview_contracts_enforce_preview_first_fields(self) -> None:
        for name in self.PREVIEWS:
            schema = self.schemas[name]
            required = set(schema["required"])
            with self.subTest(schema=name):
                self.assertEqual("preview", schema["properties"]["mode"]["const"])
                self.assertTrue({"mode", "preview_digest", "planned_write"}.issubset(required))


class V2ContractSchemaTests(ContractSchemaTests):
    DIRECTORY = V2_CONTRACT_DIR
    EXPECTED = V2_EXPECTED_SCHEMAS
    VERSION = "2.0"
    PREVIEWS = {"qa-report-preview.schema.json"}

    def test_v2_ids_do_not_reuse_v1_ids(self) -> None:
        v1_ids = {schema["$id"] for schema in load_schemas(CONTRACT_DIR).values()}
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                self.assertNotIn(schema["$id"], v1_ids)
                self.assertIn(":v2:", schema["$id"])


def _is_type(value: Any, name: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[name]


def schema_errors(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the Draft 2020-12 subset these contracts use; no third-party dependency."""
    if "$ref" in schema:
        return schema_errors(value, root["$defs"][schema["$ref"].rsplit("/", 1)[-1]], root, path)
    if "type" in schema:
        allowed = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, name) for name in allowed):
            return [f"{path}: expected {allowed}, got {type(value).__name__}"]
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            errors.append(f"{path}: length {len(value)} out of range")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if _is_type(value, "number") and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: below minimum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            errors.append(f"{path}: item count {len(value)} out of range")
        for index, item in enumerate(value):
            if "items" in schema:
                errors.extend(schema_errors(item, schema["items"], root, f"{path}[{index}]"))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        errors.extend(f"{path}: missing {name}" for name in schema.get("required", []) if name not in value)
        if schema.get("additionalProperties") is False:
            errors.extend(f"{path}: unexpected {name}" for name in value if name not in properties)
        for name, item in value.items():
            if name in properties:
                errors.extend(schema_errors(item, properties[name], root, f"{path}.{name}"))
    return errors


class ContractPorts:
    """Minimal offline ports: TestLink previews/writes and a Redmine create preview."""

    def testlink_execution(self, **kwargs):
        digest = payload_digest({key: kwargs[key] for key in ("operation_id", "testcase_external_id", "notes")})
        result = {
            "schema_version": "1.0",
            "operation_id": kwargs["operation_id"],
            "environment": kwargs["environment"],
            "mode": "preview",
            "preview_digest": digest,
            "planned_write": True,
            "target": {name: {"id": "1", "name": kwargs[name]} for name in ("project", "plan", "platform", "build")},
            "execution": {"testcase_external_id": kwargs["testcase_external_id"], "status": kwargs["status"]},
            "warnings": [],
        }
        if kwargs.get("write"):
            result = {**result, "status": "success", "execution_id": "9001", "audit_id": "tl.json"}
        return {"ok": True, "code": 0, "result": result}

    def redmine_bug(self, **kwargs):
        result = {
            "schema_version": "1.0",
            "operation_id": kwargs["operation_id"],
            "environment": kwargs["environment"],
            "mode": "preview",
            "preview_digest": hashlib.sha256(kwargs["description"].encode("utf-8")).hexdigest(),
            "planned_write": True,
            "action": "create",
            "dedupe_digest": kwargs["dedupe_marker"][-16:],
            "issue_fields": {"severity": {"label": None}},
            "issue_payload": {"project_id": kwargs["project_id"], "subject": kwargs["subject"]},
            "warnings": [],
        }
        if kwargs.get("write"):
            result = {
                **result,
                "action": "created",
                "issue": {"id": "77", "url": "https://redmine.invalid/issues/77", "subject": "", "reused": False},
                "field_verification": {"verified": True},
                "audit_id": "rm.json",
            }
        return {"ok": True, "code": 0, "result": result}

    def redmine_comment(self, **kwargs):
        raise AssertionError("no evidence comment is expected for a created issue")


def write_node_report(directory: Path, name: str, node: str, rows: list[str]) -> Path:
    path = directory / name
    path.write_text(
        "\n".join(
            [
                "Report generated on: 2026-07-13 10:00:00",
                "EMS Version: 1.2.3",
                f"Node Name: {node}",
                "Node IP: 192.0.2.10",
                "Node Chassis: MSC8000",
                "Test Results:",
                "-------------",
                *rows,
            ]
        ),
        encoding="utf-8",
    )
    return path


class MultiReportContractOutputTests(unittest.TestCase):
    def test_multi_report_preview_and_audit_match_v2_contracts(self) -> None:
        schemas = load_schemas(V2_CONTRACT_DIR)
        preview_schema = schemas["qa-report-preview.schema.json"]
        audit_schema = schemas["workflow-audit.schema.json"]
        coordinator = QaCoordinator(ContractPorts())
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first = write_node_report(root, "node-a.txt", "OLT-A", [
                "[EMS-1][test_login] Result Pass (60s)",
                "[EMS-2][test_logout] Result Skip (1s)",
            ])
            second = write_node_report(root, "node-b.txt", "OLT-B", [
                "[EMS-1][test_login] Result Fail (75s)",
                "[EMS-2][test_logout] Result Skip (2s)",
            ])
            arguments = {
                "operation_id": "operation-contract-v2",
                "environment": "sandbox",
                "project": "EMS",
                "plan": "Regression",
                "platform": "Default Platform",
                "build": "build-1",
                "reports": [{"label": "node-a", "path": str(first)}, {"label": "node-b", "path": str(second)}],
                "redmine_create_bugs": True,
                "redmine_project_id": "ems",
            }
            compact = api.qa_preview_report_artifact(coordinator=coordinator, artifact_dir=tmpdir, **arguments)
            self.assertTrue(compact["ok"], compact)
            artifact = json.loads(Path(compact["result"]["preview_artifact"]).read_text(encoding="utf-8"))
            executed = api.qa_execute_preview_artifact(
                coordinator=coordinator,
                operation_id=arguments["operation_id"],
                preview_artifact=compact["result"]["preview_artifact"],
                preview_digest=compact["result"]["preview_digest"],
                write=True,
                audit_dir=tmpdir,
            )
            self.assertTrue(executed["ok"], executed)
            audit = json.loads(Path(executed["result"]["audit_file"]).read_text(encoding="utf-8"))

        self.assertEqual([], schema_errors(compact["result"], preview_schema, preview_schema))
        self.assertEqual([], schema_errors(artifact["review"], preview_schema, preview_schema))
        self.assertIn("items", artifact["review"])
        self.assertEqual([], schema_errors(audit, audit_schema, audit_schema))
        self.assertEqual("2.0", audit["schema_version"])

    def test_validator_rejects_a_v1_single_report_field_in_v2(self) -> None:
        schema = load_schemas(V2_CONTRACT_DIR)["workflow-audit.schema.json"]
        workflow = {"report_schema": "legacy-web-ems-report-v1", "project": "EMS", "plan": "P",
                    "platform": "X", "build": "B", "redmine_create_bugs": False}
        errors = schema_errors(workflow, schema["$defs"]["workflow"], schema)
        self.assertIn("$: missing reports", errors)
        self.assertIn("$: unexpected report_schema", errors)


if __name__ == "__main__":
    unittest.main()
