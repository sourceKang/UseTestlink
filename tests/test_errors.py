import os
import unittest

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

    def test_redacts_secret_keys_in_structures(self):
        payload = redact_secrets({"devKey": "abc", "nested": [{"api_key": "def"}]})

        self.assertEqual(payload["devKey"], MASK)
        self.assertEqual(payload["nested"][0]["api_key"], MASK)

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
