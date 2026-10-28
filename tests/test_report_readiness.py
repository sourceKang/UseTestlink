from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from qa_integration_agent.coordinator import QaCoordinator
from testlink_agent_core.cli import build_parser, main as cli_main
from testlink_agent_core.commands import upload_readiness_override
from testlink_agent_core.errors import TestLinkError
from testlink_agent_core.reports import (
    JUNIT_XML_SCHEMA,
    LEGACY_WEB_EMS_SCHEMA,
    MISSING_COMPLETENESS_WARNING,
    SCHEMA_HEADER_KEY,
    parse_report,
    report_readiness,
)
from tests.test_qa_coordinator import FakePorts, multi_args, workflow_args

COMPLETE = "Completeness: Complete (2/2 selected tests have a result)"
INCOMPLETE = "Completeness: Incomplete (1/2 selected tests have a result; run interrupted)"
DRIFT = "Case ID Drift: 1 (service registered an id the testlink_case marker does not declare)"


def write_readiness_report(directory: str, *header_lines: str, name: str = "report.txt") -> Path:
    report = Path(directory) / name
    report.write_text(
        "\n".join(
            [
                "Report generated on: 2026-10-27 10:00:00",
                "Summary: 1 Pass / 0 Fail",
                *header_lines,
                "Missing Test #1: EMS-2 tests/test_x.py::test_two",
                "Test Results:",
                "-------------",
                "[EMS-1][test_login] Result Pass (60s)",
            ]
        ),
        encoding="utf-8",
    )
    return report


def legacy(**header: str) -> dict[str, str]:
    return {SCHEMA_HEADER_KEY: LEGACY_WEB_EMS_SCHEMA, **header}


class ReportReadinessTests(unittest.TestCase):
    def test_header_lines_are_parsed(self) -> None:
        with TemporaryDirectory() as tmpdir:
            header, results = parse_report(write_readiness_report(tmpdir, INCOMPLETE, DRIFT))

        self.assertEqual("Incomplete (1/2 selected tests have a result; run interrupted)", header["Completeness"])
        self.assertTrue(header["Case ID Drift"].startswith("1 "))
        self.assertEqual(["EMS-1"], [result.external_id for result in results])

    def test_readiness_rules(self) -> None:
        cases = [
            (legacy(Completeness="Complete (2/2 selected tests have a result)"), [], []),
            (legacy(Completeness="Incomplete (1/2 selected tests have a result)"), ["Completeness: Incomplete"], []),
            (legacy(Completeness="not evaluated (xdist run)"), ["Completeness: not evaluated"], []),
            (legacy(Completeness="Complete (1/1)", **{"Case ID Drift": "1 (x)"}), ["Case ID Drift: 1"], []),
            (legacy(), [], [MISSING_COMPLETENESS_WARNING]),
            ({SCHEMA_HEADER_KEY: JUNIT_XML_SCHEMA}, [], []),
        ]
        for header, issue_prefixes, warnings in cases:
            with self.subTest(header=header):
                issues, actual_warnings = report_readiness(header)
                self.assertEqual(len(issue_prefixes), len(issues))
                for issue, prefix in zip(issues, issue_prefixes):
                    self.assertTrue(issue.startswith(prefix), issue)
                self.assertEqual(warnings, actual_warnings)


class CoordinatorReadinessTests(unittest.TestCase):
    def plan(self, *header_lines: str, ports=None, **overrides):
        ports = ports or FakePorts()
        with TemporaryDirectory() as tmpdir:
            report = write_readiness_report(tmpdir, *header_lines)
            args = workflow_args(report, redmine_create_bugs=False, **overrides)
            return QaCoordinator(ports).build_plan(**args), ports

    def test_incomplete_report_is_refused_before_any_preview_call(self) -> None:
        ports = FakePorts()
        with self.assertRaises(Exception) as context:
            self.plan(INCOMPLETE, ports=ports)

        self.assertEqual("REPORT_NOT_READY", context.exception.code)
        self.assertIn("report.txt: Completeness: Incomplete", str(context.exception))
        self.assertEqual([], ports.redmine_preview_requests)

    def test_case_id_drift_is_refused(self) -> None:
        with self.assertRaises(Exception) as context:
            self.plan(COMPLETE, DRIFT)

        self.assertEqual("REPORT_NOT_READY", context.exception.code)
        self.assertIn("Case ID Drift: 1", str(context.exception))

    def test_override_reason_is_recorded_in_plan_preview_and_workflow_audit(self) -> None:
        ports = FakePorts()
        coordinator = QaCoordinator(ports)
        with TemporaryDirectory() as tmpdir:
            report = write_readiness_report(tmpdir, INCOMPLETE)
            plan = coordinator.build_plan(
                **workflow_args(
                    report,
                    redmine_create_bugs=False,
                    readiness_override_reason="user accepted: EMS-2 rerun separately",
                )
            )
            preview = QaCoordinator.public_preview(plan)
            result = coordinator.execute_plan(
                plan, confirmed_preview_digest=plan["preview_digest"], report=str(report), audit_dir=tmpdir
            )

        override = {
            "reason": "user accepted: EMS-2 rerun separately",
            "issues": ["report.txt: Completeness: Incomplete (1/2 selected tests have a result; run interrupted)"],
        }
        self.assertEqual(override, plan["readiness_override"])
        self.assertEqual(override, preview["readiness_override"])
        self.assertTrue(any(warning.startswith("Readiness override accepted (") for warning in plan["warnings"]))
        self.assertEqual(override, result["audit"]["workflow"]["readiness_override"])

    def test_ready_report_rejects_an_override_and_keeps_its_plan_shape(self) -> None:
        with self.assertRaises(Exception) as context:
            self.plan(COMPLETE, readiness_override_reason="not needed")
        self.assertEqual("INVALID_ARGUMENT", context.exception.code)

        plan, _ = self.plan(COMPLETE)
        self.assertNotIn("readiness_override", plan)
        self.assertEqual([], plan["warnings"])

    def test_missing_completeness_only_warns(self) -> None:
        plan, _ = self.plan()

        self.assertEqual(1, len(plan["items"]))
        self.assertEqual([f"report.txt: {MISSING_COMPLETENESS_WARNING}"], plan["warnings"])

    def test_multi_report_names_the_node_that_is_not_ready(self) -> None:
        ports = FakePorts()
        with TemporaryDirectory() as tmpdir:
            reports = [
                ("node-a", write_readiness_report(tmpdir, COMPLETE, name="a.txt")),
                ("node-b", write_readiness_report(tmpdir, COMPLETE, DRIFT, name="b.txt")),
            ]
            with self.assertRaises(Exception) as context:
                QaCoordinator(ports).build_plan(**multi_args(reports))

        self.assertEqual("REPORT_NOT_READY", context.exception.code)
        self.assertIn("node-b: Case ID Drift", str(context.exception))
        self.assertNotIn("node-a", str(context.exception))


class UploadReportReadinessTests(unittest.TestCase):
    def test_upload_report_applies_the_same_gate(self) -> None:
        with self.assertRaises(TestLinkError) as not_ready:
            upload_readiness_override(legacy(Completeness="Incomplete (1/2)"), None)
        self.assertEqual("REPORT_NOT_READY", not_ready.exception.code)
        with self.assertRaises(TestLinkError) as not_needed:
            upload_readiness_override(legacy(Completeness="Complete (2/2)"), "not needed")
        self.assertEqual("INVALID_ARGUMENT", not_needed.exception.code)

        self.assertIsNone(upload_readiness_override(legacy(), None))
        self.assertEqual(
            {"reason": "accepted", "issues": ["Case ID Drift: 1 (x)"]},
            upload_readiness_override(legacy(Completeness="Complete (1/1)", **{"Case ID Drift": "1 (x)"}), " accepted "),
        )

    def test_cli_error_output_carries_the_code(self) -> None:
        with TemporaryDirectory() as directory:
            report = write_readiness_report(directory, INCOMPLETE)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = cli_main(
                    [
                        "upload-report",
                        "--project", "EMS",
                        "--plan", "Regression",
                        "--platform", "Default Platform",
                        "--report", str(report),
                    ]
                )

        self.assertEqual(1, exit_code)
        self.assertEqual("REPORT_NOT_READY", json.loads(stderr.getvalue())["code"])

    def test_cli_accepts_the_override_reason(self) -> None:
        args = build_parser().parse_args(
            [
                "upload-report",
                "--project", "EMS",
                "--plan", "Regression",
                "--platform", "Default Platform",
                "--report", "report.txt",
                "--readiness-override-reason", "accepted",
            ]
        )

        self.assertEqual("accepted", args.readiness_override_reason)


if __name__ == "__main__":
    unittest.main()
