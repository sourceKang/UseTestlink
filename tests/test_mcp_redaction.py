from __future__ import annotations

import importlib
import io
import json
import os
import unittest
from unittest.mock import patch

from testlink_agent_core.errors import mask_secrets as core_mask
from redmine_mcp.errors import mask_secrets as redmine_mask


SERVERS = ("qa_integration_agent.server", "testlink_mcp.server", "redmine_mcp.server", "testlink_agent_core.server")


class McpRedactionTests(unittest.TestCase):
    def wire_response(self, server, response, framing, encoding="utf-8"):
        buffer = io.BytesIO()
        output = io.TextIOWrapper(buffer, encoding=encoding, newline="\n")
        with patch.object(server.sys, "stdout", output):
            server._write_response(response, framing)
            output.flush()
            wire = buffer.getvalue()
        output.detach()
        if framing == "content-length":
            header, wire = wire.split(b"\r\n\r\n", 1)
            self.assertEqual(len(wire), int(header.split(b":", 1)[1]))
        return json.loads(wire)

    def test_tool_success_and_failure_keep_inner_json_valid_and_masked(self):
        for name in SERVERS:
            server = importlib.import_module(name)
            for framing in ("line", "content-length"):
                for ok in (True, False):
                    with self.subTest(server=name, framing=framing, ok=ok), patch.dict(os.environ, {
                        "QA_INTEGRATION_TOOLSET": "all", "TESTLINK_MCP_TOOLSET": "all", "REDMINE_MCP_TOOLSET": "all",
                    }):
                        tools = server.handle_request({"id": 1, "method": "tools/list"})["result"]["tools"]
                        data = {"ok": ok, "result" if ok else "error": {
                            "notes": "繁體中文 TESTLINK_DEVKEY=SYNTH-TL",
                            "description": "REDMINE_API_KEY=SYNTH-RM",
                            "detail": "devKey: SYNTH-DK",
                            "api_key": "SYNTH-FIELD", "next_key": "保留欄位",
                        }}
                        with patch.object(server, "call_tool", return_value=data):
                            response = server.handle_request({"id": 7, "method": "tools/call",
                                "params": {"name": tools[0]["name"], "arguments": {}}})
                        decoded = self.wire_response(server, response, framing)
                        content = decoded["result"]["content"][0]["text"]
                        payload = json.loads(content)["result" if ok else "error"]
                        self.assertEqual("保留欄位", payload["next_key"])
                        self.assertEqual("繁體中文 TESTLINK_DEVKEY=*****", payload["notes"])
                        self.assertEqual("REDMINE_API_KEY=*****", payload["description"])
                        self.assertEqual("devKey: *****", payload["detail"])
                        self.assertEqual(not ok, decoded["result"]["isError"])
                        self.assertNotIn("SYNTH-", content)

    def test_error_data_unknown_method_and_echoed_metadata_are_redacted(self):
        for name in SERVERS:
            server = importlib.import_module(name)
            error_builder = getattr(server, "_error", None) or server._error_response
            with self.subTest(server=name), patch.dict(os.environ, {"TESTLINK_DEVKEY": "SYNTH-KNOWN"}):
                responses = [
                    error_builder("SYNTH-KNOWN", -32603, "failure SYNTH-KNOWN", {"password": "SYNTH-FIELD"}),
                    server.handle_request({"id": "SYNTH-KNOWN", "method": "SYNTH-KNOWN"}),
                    server.handle_request({"id": "SYNTH-KNOWN", "method": "initialize", "params": {
                        "protocolVersion": "SYNTH-KNOWN"}}),
                    server.handle_request({"id": "SYNTH-KNOWN", "method": "ping"}),
                ]
                for response in responses:
                    self.assertNotIn("SYNTH-", json.dumps(self.wire_response(server, response, "line")))

    def test_assignment_masking_preserves_delimiters_and_is_idempotent(self):
        for masker in (core_mask, redmine_mask):
            for key in ("TESTLINK_DEVKEY", "REDMINE_API_KEY", "devKey"):
                for source, expected in (
                    (f"{key}=SYNTH-VALUE", f"{key}=*****"),
                    (f"{key}=SYNTH-VALUE # comment", f"{key}=***** # comment"),
                    (f'prefix {key}="SYNTH VALUE" suffix', f'prefix {key}="*****" suffix'),
                    (f"{key}='SYNTH VALUE'", f"{key}='*****'"),
                    (f"{key}=SYNTH-VALUE, next", f"{key}=*****, next"),
                ):
                    with self.subTest(masker=masker.__module__, source=source):
                        masked = masker(source)
                        self.assertEqual(expected, masked)
                        self.assertEqual(masked, masker(masked))
                serialized = json.dumps({"notes": f"{key}=SYNTH-VALUE", "next": "keep"}, indent=2)
                parsed = json.loads(masker(serialized))
                self.assertEqual("keep", parsed["next"])
                self.assertEqual(f"{key}=*****", parsed["notes"])

    def test_wire_uses_utf8_even_when_stdout_text_encoding_is_ascii(self):
        for name in SERVERS:
            with self.subTest(server=name):
                decoded = self.wire_response(importlib.import_module(name),
                    {"jsonrpc": "2.0", "id": 1, "result": {"message": "繁體中文"}}, "line", encoding="ascii")
                self.assertEqual("繁體中文", decoded["result"]["message"])

    def test_run_masks_unexpected_tool_exceptions(self):
        for name in SERVERS:
            server = importlib.import_module(name)
            with self.subTest(server=name), patch.dict(os.environ, {
                "TESTLINK_DEVKEY": "SYNTH-KNOWN", "QA_INTEGRATION_TOOLSET": "all",
                "TESTLINK_MCP_TOOLSET": "all", "REDMINE_MCP_TOOLSET": "all",
            }):
                tools = server.handle_request({"id": 1, "method": "tools/list"})["result"]["tools"]
                request = {"id": 2, "method": "tools/call", "params": {"name": tools[0]["name"]}}
                buffer = io.BytesIO()
                output = io.TextIOWrapper(buffer, encoding="utf-8")
                with patch.object(server, "_read_message", side_effect=[(request, "line"), (None, "line")]), \
                     patch.object(server, "call_tool", side_effect=RuntimeError("failure SYNTH-KNOWN")), \
                     patch.object(server.sys, "stdout", output), patch.object(server.sys, "stderr", io.StringIO()), \
                     patch("socket.socket", side_effect=AssertionError("network forbidden")):
                    server.run() if name == "qa_integration_agent.server" else server.run(health_check=False)
                    output.flush()
                    wire = buffer.getvalue().decode("utf-8")
                output.detach()
                self.assertNotIn("SYNTH-KNOWN", wire)
                self.assertEqual(-32603, json.loads(wire)["error"]["code"])
