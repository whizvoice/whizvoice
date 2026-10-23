import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import UserApiKeySetRequest


class TestUserApiKeySetRequest(unittest.TestCase):
    def test_strips_trailing_newline_from_key_value(self):
        req = UserApiKeySetRequest(key_name="claude_api_key", key_value="sk-ant-abc\n")
        self.assertEqual(req.key_value, "sk-ant-abc")

    def test_strips_surrounding_whitespace_from_key_value(self):
        req = UserApiKeySetRequest(key_name="claude_api_key", key_value="  sk-ant-abc \r\n")
        self.assertEqual(req.key_value, "sk-ant-abc")

    def test_strips_asana_token_too(self):
        req = UserApiKeySetRequest(key_name="asana_access_token", key_value="1/1234:abcd\n")
        self.assertEqual(req.key_value, "1/1234:abcd")

    def test_none_key_value_stays_none(self):
        req = UserApiKeySetRequest(key_name="claude_api_key", key_value=None)
        self.assertIsNone(req.key_value)

    def test_whitespace_only_key_value_becomes_empty_clear(self):
        req = UserApiKeySetRequest(key_name="claude_api_key", key_value="  \n")
        self.assertEqual(req.key_value, "")


if __name__ == "__main__":
    unittest.main()
