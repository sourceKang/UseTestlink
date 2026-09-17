from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from qa_mcp_contracts import payload_digest
from qa_integration_agent.api import qa_read_preview_artifact
from qa_integration_agent.artifacts import write_preview_artifact
from qa_integration_agent.server import handle_request


class PreviewReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.plan = {
            "schema_version": "1.0", "operation_id": "offline-review",
            "environment": "sandbox", "target": {"project": "測試專案"},
            "redmine_create_bugs": False,
            "items": [{"result": {"external_id": f"QA-{i}"},
                       "testlink_request": {"notes": f"步驟 {i}"}} for i in range(7)],
            "warnings": ["review warning"], "ignored": [{"external_id": "QA-9"}],
        }
        self.plan["preview_digest"] = payload_digest(self.plan)
        self.path = write_preview_artifact(self.plan, {"items": ["untrusted copy"]}, self.directory.name)
        self.kwargs = {"operation_id": self.plan["operation_id"], "preview_artifact": str(self.path),
                       "preview_digest": self.plan["preview_digest"]}

    def test_pages_cover_exact_items_without_network_or_writes(self):
        before = self.path.read_bytes()
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             patch("qa_integration_agent.api.QaCoordinator", side_effect=AssertionError("upstream forbidden")):
            first = qa_read_preview_artifact(**self.kwargs)["result"]
            last = qa_read_preview_artifact(**self.kwargs, offset=first["next_offset"])["result"]
        self.assertEqual(self.plan["items"], first["entries"] + last["entries"])
        self.assertEqual(5, first["next_offset"])
        self.assertIsNone(last["next_offset"])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual({"items": 7, "warnings": 1, "ignored": 1}, first["section_counts"])

    def test_warnings_ignored_and_empty_end_page(self):
        for section in ("warnings", "ignored"):
            result = qa_read_preview_artifact(**self.kwargs, section=section)["result"]
            self.assertEqual(self.plan[section], result["entries"])
        self.assertEqual([], qa_read_preview_artifact(**self.kwargs, offset=7)["result"]["entries"])

    def test_invalid_pagination_rejected(self):
        for extra in ({"offset": -1}, {"offset": 8}, {"offset": True}, {"offset": 1.5},
                      {"limit": 0}, {"limit": 51}, {"limit": False}, {"limit": "5"},
                      {"section": "plan"}):
            with self.subTest(extra=extra):
                result = qa_read_preview_artifact(**self.kwargs, **extra)
                self.assertFalse(result["ok"])
                self.assertEqual("INVALID_PAGE", result["error"]["error"]["code"])

    def test_identity_and_digest_must_match(self):
        for key in ("operation_id", "preview_digest"):
            result = qa_read_preview_artifact(**{**self.kwargs, key: "wrong"})
            self.assertEqual("PREVIEW_MISMATCH", result["error"]["error"]["code"])

    def test_tampering_between_pages_is_rejected(self):
        self.assertTrue(qa_read_preview_artifact(**self.kwargs)["ok"])
        artifact = json.loads(self.path.read_text(encoding="utf-8"))
        artifact["plan"]["items"][6]["testlink_request"]["notes"] = "changed"
        self.path.write_text(json.dumps(artifact), encoding="utf-8")
        result = qa_read_preview_artifact(**self.kwargs, offset=5)
        self.assertEqual("PREVIEW_MISMATCH", result["error"]["error"]["code"])

    def test_review_copy_cannot_override_verified_plan(self):
        result = qa_read_preview_artifact(**self.kwargs, limit=1)["result"]
        self.assertEqual([self.plan["items"][0]], result["entries"])
        self.assertNotIn("untrusted copy", json.dumps(result))

    def test_response_redacts_known_secret_and_secret_keys(self):
        self.plan.pop("preview_digest")
        self.plan["items"][0]["testlink_request"] = {"notes": "offline-secret-value", "password": "dummy-password"}
        self.plan["preview_digest"] = payload_digest(self.plan)
        # Simulate a valid older artifact; redaction must also happen at read time.
        artifact = json.loads(self.path.read_text(encoding="utf-8"))
        artifact.update(plan=self.plan, preview_digest=self.plan["preview_digest"])
        self.path.write_text(json.dumps(artifact), encoding="utf-8")
        with patch.dict(os.environ, {"TESTLINK_DEVKEY": "offline-secret-value"}):
            result = qa_read_preview_artifact(**{**self.kwargs, "preview_digest": self.plan["preview_digest"]})
        self.assertTrue(result["ok"])
        self.assertNotIn("offline-secret-value", json.dumps(result))
        self.assertNotIn("dummy-password", json.dumps(result))

    def test_malformed_and_missing_artifacts_fail(self):
        self.path.write_text("not json", encoding="utf-8")
        self.assertFalse(qa_read_preview_artifact(**self.kwargs)["ok"])
        self.path.unlink()
        self.assertEqual("PREVIEW_ARTIFACT_NOT_FOUND", qa_read_preview_artifact(**self.kwargs)["error"]["error"]["code"])

    def test_tool_is_exposed_in_import_but_not_legacy(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "qa_read_preview_artifact", "arguments": self.kwargs}}
        with patch.dict(os.environ, {"QA_INTEGRATION_TOOLSET": "import"}):
            response = handle_request(request)
            self.assertFalse(response["result"]["isError"])
        with patch.dict(os.environ, {"QA_INTEGRATION_TOOLSET": "legacy"}):
            self.assertEqual(-32602, handle_request(request)["error"]["code"])
