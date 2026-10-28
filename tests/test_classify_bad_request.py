import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import classify_claude_bad_request

USAGE_LIMIT_TEXT = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'You have reached your specified API usage limits. "
    "You will regain access on 2026-11-01 at 00:00 UTC.'}, 'request_id': 'req_011CgS41BrteRgtqACCcfEeV'}"
)


class TestClassifyClaudeBadRequest(unittest.TestCase):
    def test_usage_limit_returns_dedicated_code(self):
        code, _ = classify_claude_bad_request(USAGE_LIMIT_TEXT)
        self.assertEqual(code, "CLAUDE_USAGE_LIMIT")

    def test_usage_limit_message_includes_reset_time_and_guidance(self):
        _, message = classify_claude_bad_request(USAGE_LIMIT_TEXT)
        self.assertIn("spending limit", message)
        self.assertIn("2026-11-01 at 00:00 UTC", message)
        self.assertIn("Anthropic Console", message)
        self.assertIn("Settings", message)
        self.assertNotIn("new conversation", message)

    def test_usage_limit_without_parseable_date_still_classified(self):
        text = "Error code: 400 - {'message': 'You have reached your specified API usage limits.'}"
        code, message = classify_claude_bad_request(text)
        self.assertEqual(code, "CLAUDE_USAGE_LIMIT")
        self.assertIn("spending limit", message)
        self.assertNotIn("Access resumes", message)

    def test_unrelated_bad_request_falls_back_to_generic(self):
        text = "Error code: 400 - {'message': 'messages: text content blocks must be non-empty'}"
        code, message = classify_claude_bad_request(text)
        self.assertEqual(code, "CLAUDE_API_ERROR")
        self.assertIn("new conversation", message)

    def test_tool_use_id_mismatch_is_history_error(self):
        text = "Error code: 400 - {'message': 'unexpected tool_use_id found in tool_result blocks'}"
        code, message = classify_claude_bad_request(text)
        self.assertEqual(code, "CONVERSATION_HISTORY_ERROR")
        self.assertIn("new conversation", message)


if __name__ == "__main__":
    unittest.main()
