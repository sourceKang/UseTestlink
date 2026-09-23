import os
import unittest

import json

from testlink_agent_core.errors import MASK, TestLinkError, mask_secrets, redact_secrets


class ErrorTests(unittest.TestCase):
    def test_masks_known_devkey_from_text(self):
        old_value = os.environ.get("TESTLINK_DEVKEY")
        os.environ["TESTLINK_DEVKEY"] = "super-secret-key"
        try:
            masked = mask_secrets("failed with devKey=super-secret-key")
        finally:
            if old_value is None:
                os.environ.pop("TESTLINK_DEVKEY", None)
            else:
                os.environ["TESTLINK_DEVKEY"] = old_value

        self.assertNotIn("super-secret-key", masked)
        self.assertIn(MASK, masked)

    def test_assignment_masking_keeps_trailing_cjk_text(self):
        cases = [
            ("TESTLINK_DEVKEY=abc123", "，後續內容必須保留。"),
            ("REDMINE_API_KEY=abc123", "。此行之後的中文敘述必須保留"),
            ("devKey: abc123", "、緊接著的欄位不得被吃掉"),
            ("TESTLINK_DEVKEY=abc123", "後面沒有標點也要保留"),
        ]
        for assignment, tail in cases:
            with self.subTest(assignment=assignment, tail=tail):
                masked = mask_secrets(assignment + tail)
                self.assertNotIn("abc123", masked)
                self.assertIn(MASK, masked)
                self.assertTrue(masked.endswith(tail), masked)

    def test_assignment_masking_keeps_serialized_json_parseable(self):
        payload = json.dumps({"notes": "設定內容：REDMINE_API_KEY=abc123，其餘必須保留。"}, ensure_ascii=False)
        masked = mask_secrets(payload)

        self.assertNotIn("abc123", masked)
        self.assertEqual("設定內容：REDMINE_API_KEY=*****，其餘必須保留。", json.loads(masked)["notes"])

    def test_redacts_secret_keys_in_structures(self):
        payload = redact_secrets(
            {
                "devKey": "abc",
                "nested": [{"api_key": "def"}],
                "password": "ghi",
                "token": "jkl",
            }
        )

        self.assertEqual(payload["devKey"], MASK)
        self.assertEqual(payload["nested"][0]["api_key"], MASK)
        self.assertEqual(payload["password"], MASK)
        self.assertEqual(payload["token"], MASK)

    def test_testlink_error_masks_message(self):
        old_value = os.environ.get("TESTLINK_DEVKEY")
        os.environ["TESTLINK_DEVKEY"] = "super-secret-key"
        try:
            error = TestLinkError("devKey super-secret-key failed")
        finally:
            if old_value is None:
                os.environ.pop("TESTLINK_DEVKEY", None)
            else:
                os.environ["TESTLINK_DEVKEY"] = old_value

        self.assertNotIn("super-secret-key", str(error))


if __name__ == "__main__":
    unittest.main()
